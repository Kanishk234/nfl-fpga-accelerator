"""
NFL FPGA Predictor (GBDT) — local web UI.

The GBDT twin of phase7_deploy/ui/webapp.py. Backend is the Python standard
library only (http.server) + pyserial, so it runs natively on Windows where the
board's COM port lives; feature bytes come from games_catalog_gbdt.json (built
once in WSL by export_catalog_gbdt.py).

    python phase7_deploy_conifer/ui/webapp_gbdt.py            # http://127.0.0.1:8714
    python phase7_deploy_conifer/ui/webapp_gbdt.py --port 9000 --no-browser

It deliberately serves the SAME phase7_deploy/ui/index.html as the MLP app
rather than forking 280 lines of near-identical HTML. That page is model-driven:
everything build-specific (header badge, pipeline labels, spread precision, the
raw-word denominator) arrives in the `model` block of /api/bootstrap. See
PHASE7_CONIFER_COMPLETE.md "One page, two bitstreams".

Default HTTP port is 8714, one above the MLP's 8713, so both UIs can run at once
— though only one may hold the COM port at a time, and only one bitstream is
loaded on the board.
"""
import argparse
import json
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import serial.tools.list_ports

from phase7_deploy.inference.game_catalog import GameCatalog
from phase7_deploy_conifer.inference.fpga_client_gbdt import GBDTClient

REPO = Path(__file__).resolve().parents[2]
# Shared with the MLP app — single source of truth for the page.
INDEX = REPO / 'phase7_deploy' / 'ui' / 'index.html'
CATALOG_PATH = REPO / 'phase7_deploy_conifer' / 'games_catalog_gbdt.json'
CATALOG = None

# Tells index.html which bitstream it is talking to.
MODEL = {
    'name': 'GBDT',
    'sub': '2-stage XGBoost · conifer',
    'steps': ['Encode 63B', 'TX →', 'FPGA GBDT ×2', '← RX 8B'],
    'raw_denom': 4096,        # ap_fixed<24,12>
    'spread_decimals': 2,     # full-width result, not a rounded byte
}

_client = None
_lock = threading.Lock()


def _get_client(port):
    global _client
    if _client is not None and _client.port != port:
        _client.disconnect()
        _client = None
    if _client is None or not _client.is_connected():
        _client = GBDTClient(port)
        _client.connect()
    return _client


def list_ports():
    return [{'device': p.device, 'desc': p.description or p.device}
            for p in serial.tools.list_ports.comports()]


def bootstrap():
    games = [{'gid': g['gid'], 'season': g['season'], 'week': g['week'],
              'home': g['home'], 'away': g['away'],
              'actual_winner': g.get('actual_winner'),
              'actual_spread': g.get('actual_spread')}
             for g in CATALOG.games]
    return {'generated': CATALOG.generated, 'ports': list_ports(),
            'games': games, 'model': MODEL}


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

    def log_message(self, *a):
        pass


def main():
    global CATALOG
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8714, help='HTTP port')
    ap.add_argument('--no-browser', action='store_true')
    args = ap.parse_args()

    try:
        CATALOG = GameCatalog(CATALOG_PATH)
    except FileNotFoundError:
        print(f"{CATALOG_PATH.name} not found — build it once in WSL:")
        print("  source venv/bin/activate && "
              "python phase7_deploy_conifer/export_catalog_gbdt.py")
        sys.exit(1)

    url = f"http://127.0.0.1:{args.port}"
    print(f"NFL FPGA Predictor (GBDT) — {len(CATALOG.games)} games loaded")
    print(f"Serving {url}   (Ctrl+C to stop)")
    print("Board must be running top_gbdt.bit — the MLP bitstream speaks a "
          "different protocol and will not answer these packets.")
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == '__main__':
    main()
