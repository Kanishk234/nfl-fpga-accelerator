#!/usr/bin/env python3
"""Parse Vivado utilization + timing reports into artifacts/synthesis_report.json."""
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = Path(__file__).resolve().parent
ARTIFACT_PATH = REPO_ROOT / "artifacts" / "synthesis_report.json"

UTIL_REPORT  = SCRIPT_DIR / "utilization_report.txt"
TIMING_REPORT = SCRIPT_DIR / "timing_report.txt"

TARGETS = {
    "LUT":  20_800,
    "FF":   41_600,
    "BRAM": 100,
    "DSP":  90,
}

def parse_utilization(path: Path) -> dict:
    result = {}
    text = path.read_text()
    patterns = {
        "LUT":  r"\|\s*Slice LUTs\s*\|\s*(\d[\d,]*)\s*\|[^|]*\|\s*(\d[\d,]*)",
        "FF":   r"\|\s*Slice Registers\s*\|\s*(\d[\d,]*)\s*\|[^|]*\|\s*(\d[\d,]*)",
        "BRAM": r"\|\s*Block RAM Tile\s*\|\s*(\d[\d,]*)\s*\|[^|]*\|\s*(\d[\d,]*)",
        "DSP":  r"\|\s*DSPs\s*\|\s*(\d[\d,]*)\s*\|[^|]*\|\s*(\d[\d,]*)",
    }
    for resource, pat in patterns.items():
        m = re.search(pat, text)
        if m:
            used  = int(m.group(1).replace(",", ""))
            total = int(m.group(2).replace(",", ""))
            result[resource] = {
                "used":    used,
                "total":   total,
                "pct":     round(used / total * 100, 2),
                "budget":  TARGETS[resource],
                "ok":      used <= TARGETS[resource],
            }
        else:
            print(f"WARNING: Could not parse {resource} from {path}", file=sys.stderr)
    return result

def parse_timing(path: Path) -> dict:
    text = path.read_text()
    wns = None
    m = re.search(r"WNS\(ns\)\s+TNS\(ns\)[^\n]*\n\s*-+\s*-+[^\n]*\n\s*(-?\d+\.\d+)", text)
    if m:
        wns = float(m.group(1))
    else:
        # fallback: look for "Slack (MET|VIOLATED) : <N> ns"
        m2 = re.search(r"Slack\s+\((?:MET|VIOLATED)\)\s*:\s*(-?\d+\.\d+)\s*ns", text)
        if m2:
            wns = float(m2.group(1))
    return {
        "WNS_ns":       wns,
        "timing_ok":    wns is not None and wns >= 0.0,
        "timing_note":  "WNS >= 0 required for 100 MHz operation",
    }

def main():
    if not UTIL_REPORT.exists():
        print(f"ERROR: {UTIL_REPORT} not found. Copy it from the Vivado run directory.", file=sys.stderr)
        sys.exit(1)
    if not TIMING_REPORT.exists():
        print(f"ERROR: {TIMING_REPORT} not found. Copy it from the Vivado run directory.", file=sys.stderr)
        sys.exit(1)

    report = {
        "source": {
            "utilization": str(UTIL_REPORT),
            "timing":      str(TIMING_REPORT),
        },
        "resources": parse_utilization(UTIL_REPORT),
        "timing":    parse_timing(TIMING_REPORT),
    }

    ARTIFACT_PATH.parent.mkdir(exist_ok=True)
    ARTIFACT_PATH.write_text(json.dumps(report, indent=2))
    print(f"Wrote {ARTIFACT_PATH}")

    # Summary
    print("\n--- Resource Summary ---")
    all_ok = True
    for res, data in report["resources"].items():
        status = "OK" if data["ok"] else "OVER BUDGET"
        if not data["ok"]:
            all_ok = False
        print(f"  {res:5s}: {data['used']:6,} / {data['total']:6,} ({data['pct']:.1f}%) — {status}")
    t = report["timing"]
    wns_str = f"{t['WNS_ns']:.3f} ns" if t["WNS_ns"] is not None else "parse error"
    timing_status = "OK" if t["timing_ok"] else "TIMING VIOLATION"
    if not t["timing_ok"]:
        all_ok = False
    print(f"\n  WNS: {wns_str} — {timing_status}")
    print(f"\nOverall: {'PASS' if all_ok else 'FAIL'}")

if __name__ == "__main__":
    main()
