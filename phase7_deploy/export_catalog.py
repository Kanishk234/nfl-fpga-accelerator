"""
Precompute the feature bytes for every game into a JSON catalog.

WHY THIS EXISTS: the UI must run on Windows (that's where the board's COM port lives),
but feature encoding needs pandas/scikit-learn + the frozen scaler, which only live in
the WSL venv. So we do the pandas work ONCE here (in WSL) and bake the result into
phase7_deploy/games_catalog.json. The UI then needs only pyserial — no data stack — and
runs natively on Windows/COM8.

Encoding is delegated to FeatureBuilder._encode_row, so every byte in the catalog is
byte-identical to the Phase 6 sim/golden vectors (the scaler is never refit).

Run from project root in WSL:
    source venv/bin/activate
    python phase7_deploy/export_catalog.py
"""
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from phase7_deploy.inference.feature_builder import FeatureBuilder

OUT = Path('phase7_deploy/games_catalog.json')


def main():
    b = FeatureBuilder()
    games = b.games

    catalog = []
    skipped = 0
    for _, row in games.iterrows():
        # Only played games with complete features are useful for board inference.
        if row.get('home_win') is None or str(row.get('home_win')) == 'nan':
            skipped += 1
            continue
        try:
            feats = b._encode_row(row)          # 21 uint8 — identical to sim encoding
            if len(feats) != 21:
                raise ValueError('not 21 bytes')
        except Exception:
            skipped += 1
            continue

        has_score = ('home_score' in row and 'away_score' in row
                     and str(row['home_score']) != 'nan')
        catalog.append({
            'season':        int(row['season']),
            'week':          int(row['week']),
            'home':          str(row['home_team']),
            'away':          str(row['away_team']),
            'bytes':         feats,
            'actual_winner': (str(row['home_team']) if int(row['home_win']) == 1
                              else str(row['away_team'])),
            'actual_spread': (int(row['home_score'] - row['away_score'])
                              if has_score else None),
        })

    catalog.sort(key=lambda g: (g['season'], g['week'], g['home']))
    payload = {
        'generated':    datetime.now().isoformat(timespec='seconds'),
        'feature_count': 21,
        'protocol':     'AA + 21 bytes + xor-checksum -> 55 + win_u8 + spread_i8 + status',
        'n_games':      len(catalog),
        'games':        catalog,
    }
    OUT.write_text(json.dumps(payload))
    print(f"Wrote {OUT} — {len(catalog)} games "
          f"({len(set((g['season'],g['week']) for g in catalog))} season/weeks), "
          f"skipped {skipped} unplayed/incomplete.")


if __name__ == '__main__':
    main()
