"""
One-packet smoke test for the GBDT bitstream. Run this first whenever you
reprogram or reconnect the board.

Sends validation vector 0 (a real 2021 game) and checks the response is
well-formed AND bit-exact against the phase-6 golden. Windows-friendly: needs
only pyserial + validation_vectors.json (built in WSL by export_catalog_gbdt.py).

    python gbdt/phase7_deploy/board/verify_uart_gbdt.py COM8
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from gbdt.phase7_deploy.inference.fpga_client_gbdt import GBDTClient, from_fixed

VECTORS = Path(__file__).resolve().parents[1] / 'validation_vectors.json'


def smoke_test(port: str) -> bool:
    print(f"GBDT UART smoke test on {port}...")

    try:
        v = json.loads(VECTORS.read_text())['vectors'][0]
        features = v['bytes']
        print(f"Using vector 0: {v['home']} (H) vs {v['away']} (A), "
              f"{v['season']} week {v['week']}")
    except FileNotFoundError:
        print(f"ERROR: {VECTORS} not found. Build it in WSL first:")
        print("  source venv/bin/activate && "
              "python gbdt/phase7_deploy/export_catalog_gbdt.py")
        return False

    # Vector 0's first feature byte happens to be 0xAA — the request SOF marker.
    # If the board's framing mishandled it we would see a desync here, not a
    # clean response. (Phase 6's SOF-collision test covers this deliberately.)
    print(f"  first feature byte = 0x{features[0]:02X}"
          f"{'  <- same as SOF, exercises the collision path' if features[0] == 0xAA else ''}")

    with GBDTClient(port) as client:
        print(f"Sending 65-byte packet ({len(features)} feature bytes)...")
        result = client.run_inference(features)

    exp_win, exp_spread = v['exp_win'], v['exp_spread']
    exact = (result['raw_win'] == exp_win and result['raw_spread'] == exp_spread)

    print("\nResult:")
    print(f"  Win probability: {result['win_pct_str']}  "
          f"(raw {result['raw_win']}/4096, golden {exp_win})")
    print(f"  Point spread:    {result['spread']:+.3f}  "
          f"(raw 0x{result['raw_spread']:06X}, golden 0x{exp_spread:06X} "
          f"= {from_fixed(exp_spread):+.3f})")
    print(f"  Status:          {result['status']}")
    print(f"  Latency:         {result['latency_ms']:.1f} ms")
    print(f"  Bit-exact:       {'YES' if exact else 'NO'}")

    if result['status'] != 'OK':
        print("\nFAIL: board did not return status OK.")
        print("  NACK    -> checksum mismatch; check the UART wiring/baud.")
        print("  TIMEOUT -> gbdt_controller watchdog; press BTNC to reset.")
        return False

    if not exact:
        print("\nFAIL: response is well-formed but does NOT match the golden.")
        print("  Most likely the board is running a different bitstream "
              "(the MLP top.bit?) or a stale build.")
        return False

    if result['latency_ms'] > 100:
        print(f"\nWARNING: high latency ({result['latency_ms']:.0f} ms). Expected "
              f"~7-16 ms. See PHASE7_COMPLETE.md on the FTDI latency timer.")

    print("\nSmoke test PASSED — board is running the GBDT model, bit-exact vs sim.")
    return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('port', help='COM port e.g. COM8 or /dev/ttyUSB0')
    args = parser.parse_args()
    sys.exit(0 if smoke_test(args.port) else 1)
