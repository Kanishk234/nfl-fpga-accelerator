"""
NFL FPGA Predictor — local web UI.

A modern browser front-end for running inference on the Basys 3. Backend is the Python
standard library only (http.server) + pyserial — no Flask, no extra installs. Feature
bytes come from games_catalog.json (built once in WSL by export_catalog.py), so this runs
natively on Windows where the board's COM port lives.

    python mlp/phase7_deploy/ui/webapp.py            # opens http://127.0.0.1:8713
    python mlp/phase7_deploy/ui/webapp.py --port 9000 --no-browser

The board is driven through the exact same FPGAClient the CLI/desktop tools use: the win%
and spread are the 4 bytes the FPGA returns — nothing is computed on the laptop.
"""
import argparse
import json
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

import serial.tools.list_ports
from mlp.phase7_deploy.inference.fpga_client import FPGAClient
from mlp.phase7_deploy.inference.game_catalog import GameCatalog

HERE     = Path(__file__).parent
INDEX    = HERE / 'index.html'
CATALOG  = None          # GameCatalog, loaded at startup

# Describes THIS bitstream to the front-end. index.html is model-driven so the
# GBDT server (gbdt/phase7_deploy/ui/webapp_gbdt.py) can serve the same page
# with a different block — the two builds have different UART protocols and must
# never be confused for each other.
MODEL = {
    'name': 'MLP',
    'sub': '4-layer dense · hls4ml',
    'steps': ['Encode 21B', 'TX →', 'FPGA MLP', '← RX 4B'],
    'raw_denom': 256,
    'spread_decimals': 0,
}

# One persistent serial connection, guarded so overlapping requests serialize.
_client = None
_lock   = threading.Lock()


def _get_client(port):
    global _client
    if _client is not None and _client.port != port:
        _client.disconnect()
        _client = None
    if _client is None or not _client.is_connected():
        _client = FPGAClient(port)
        _client.connect()
    return _client


def list_ports():
    return [{'device': p.device, 'desc': p.description or p.device}
            for p in serial.tools.list_ports.comports()]


def bootstrap():
    # Everything the front-end needs to build its dropdowns, minus the feature bytes
    # (those stay server-side and are looked up by gid at inference time).
    games = [{'gid': g['gid'], 'season': g['season'], 'week': g['week'],
              'home': g['home'], 'away': g['away'],
              'actual_winner': g.get('actual_winner'),
              'actual_spread': g.get('actual_spread')}
             for g in CATALOG.games]
    return {'generated': CATALOG.generated, 'ports': list_ports(), 'games': games,
            'model': MODEL}


def infer(port, gid):
    game = CATALOG.by_gid(int(gid))
    with _lock:
        client = _get_client(port)
        result = client.run_inference(game['bytes'])
    win = result['win_prob']
    return {
        'home': game['home'], 'away': game['away'],
        'season': game['season'], 'week': game['week'],
        'win_prob': win,
        'winner': game['home'] if win >= 0.5 else game['away'],
        'win_display': win if win >= 0.5 else 1 - win,
        'spread': result['spread'],
        'status': result['status'],
        'latency_ms': result['latency_ms'],
        'raw_win': result['raw_win'],
        'actual_winner': game.get('actual_winner'),
        'actual_spread': game.get('actual_spread'),
    }


class Handler(BaseHTTPRequestHandler):

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ('/', '/index.html'):
            body = INDEX.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == '/api/bootstrap':
            self._json(bootstrap())
        elif path == '/api/ports':
            self._json({'ports': list_ports()})
        else:
            self._json({'error': 'not found'}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get('Content-Length', 0))
        payload = json.loads(self.rfile.read(length) or b'{}')
        if path == '/api/infer':
            try:
                self._json(infer(payload['port'], payload['gid']))
            except Exception as e:
                self._json({'error': str(e)}, 500)
        else:
            self._json({'error': 'not found'}, 404)

    def log_message(self, *a):        # keep the console quiet
        pass


def main():
    global CATALOG
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8713, help='HTTP port')
    ap.add_argument('--no-browser', action='store_true')
    args = ap.parse_args()

    try:
        CATALOG = GameCatalog()
    except FileNotFoundError:
        print("games_catalog.json not found — build it once in WSL:")
        print("  source venv/bin/activate && python mlp/phase7_deploy/export_catalog.py")
        sys.exit(1)

    url = f"http://127.0.0.1:{args.port}"
    print(f"NFL FPGA Predictor — {len(CATALOG.games)} games loaded")
    print(f"Serving {url}   (Ctrl+C to stop)")
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == '__main__':
    main()
