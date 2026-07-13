"""
Quick smoke test before running the full UI.
Sends one known packet and verifies the response is well-formed.
Run this first whenever you reconnect the board.
"""

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from phase7_deploy.inference.fpga_client import FPGAClient

# First game's feature bytes from the Phase-6 functional vectors (hex $readmemh file,
# one game of 21 bytes per line). golden.csv gives the matching team names.
VECTORS_PATH = 'phase6_sim/functional/tb_inputs.mem'
GOLDEN_PATH  = 'phase6_sim/functional/golden.csv'


def smoke_test(port: str) -> bool:
    print(f"UART smoke test on {port}...")

    try:
        first    = open(VECTORS_PATH).readline().split()
        features = [int(b, 16) for b in first]
        if len(features) != 21:
            raise ValueError(f"expected 21 bytes, got {len(features)}")
        g0 = next(csv.DictReader(open(GOLDEN_PATH)))
        print(f"Using: {g0.get('home','?')} vs {g0.get('away','?')}")
    except Exception:
        features = [128] * 21
        print("(Using synthetic features — golden vectors not found)")

    with FPGAClient(port) as client:
        print(f"Sending test packet ({len(features)} features)...")
        result = client.run_inference(features)

    print(f"\nResult:")
    print(f"  Win probability: {result['win_pct_str']}")
    print(f"  Point spread:    {result['spread']:+d}")
    print(f"  Status:          {result['status']}")
    print(f"  Latency:         {result['latency_ms']:.1f}ms")

    if result['status'] != 'OK':
        print("\nWARNING: NACK received — board checksum error")
        print("Check UART connection and press BTNC to reset board")
        return False

    if result['latency_ms'] > 100:
        print(f"\nWARNING: High latency ({result['latency_ms']:.0f}ms)")
        print("Expected <5ms. Check for other applications using the COM port.")

    print("\nSmoke test PASSED — board is responding correctly")
    return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('port', help='COM port e.g. COM3 or /dev/ttyUSB0')
    args   = parser.parse_args()
    smoke_test(args.port)
