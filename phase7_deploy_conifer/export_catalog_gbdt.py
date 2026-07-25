"""
Precompute the 63 wire bytes for every game into games_catalog_gbdt.json, and
bundle the 100 phase-6 validation vectors into validation_vectors.json.

Same split-across-the-boundary trick as the MLP flow (see PHASE7_COMPLETE.md
"Live UI"): the UI must run on Windows, where the board's COM port lives, but
feature encoding needs pandas + the parquet, which live only in the WSL venv.
So we do the pandas work ONCE here and bake the result into a JSON the UI can
read with nothing but the standard library.

Encoding is delegated to GBDTFeatureBuilder._encode_row, so every byte here is
byte-identical to phase6_sim_conifer/chain_golden/tb_inputs.mem — the board
receives precisely what XSIM verified.

Run from project root in WSL:
    source venv/bin/activate
    python phase7_deploy_conifer/export_catalog_gbdt.py
"""
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phase7_deploy_conifer.inference.feature_builder_gbdt import GBDTFeatureBuilder

OUT = Path('phase7_deploy_conifer/games_catalog_gbdt.json')
VEC_OUT = Path('phase7_deploy_conifer/validation_vectors.json')
CHAIN = Path('phase6_sim_conifer/chain_golden')
SIM_RESULTS = Path('phase6_sim_conifer/sim_results.csv')
VAL_SEASONS = [2021, 2022]      # must match make_chain_golden.py


def export_validation_vectors(b):
    """Bundle the 100 phase-6 vectors + expected words + team names into one
    JSON, so validation/golden_vector_test_gbdt.py runs on Windows with only
    pyserial — no pandas, no .mem parsing, no re-derivation of the val split.

    The inputs are re-encoded here from the parquet and asserted equal to
    chain_golden/tb_inputs.mem, so this file cannot silently drift from what
    XSIM actually simulated.
    """
    import csv

    golden_words = [int(l.strip(), 16)
                    for l in (CHAIN / 'tb_inputs.mem').read_text().split() if l.strip()]
    golden_fixed = [int(l.strip(), 16)
                    for l in (CHAIN / 'golden_fixed.mem').read_text().split() if l.strip()]
    n = len(golden_words) // 21

    sim = {}
    if SIM_RESULTS.exists():
        with open(SIM_RESULTS) as f:
            sim = {int(r['game_idx']): r for r in csv.DictReader(f)}

    va = b.games[b.games['season'].isin(VAL_SEASONS)]
    vectors = []
    for g in range(n):
        row = va.iloc[g]
        words = b.fixed_words(row)
        expected = golden_words[g * 21:(g + 1) * 21]
        if words != expected:
            raise AssertionError(
                f"game {g}: host encoding != tb_inputs.mem — the val split or the "
                f"quantization drifted from make_chain_golden.py"
            )
        entry = {
            'game_idx': g,
            'home': str(row['home_team']),
            'away': str(row['away_team']),
            'season': int(row['season']),
            'week': int(row['week']),
            'bytes': b._encode_row(row),
            # Expected board output: the phase-6 golden (== XSIM, proven bit-exact).
            'exp_win': golden_fixed[g * 2],
            'exp_spread': golden_fixed[g * 2 + 1],
            'actual_winner': (str(row['home_team']) if int(row['home_win']) == 1
                              else str(row['away_team'])),
        }
        if g in sim:
            entry['sim_win'] = int(sim[g]['win_prob_fixed'])
            entry['sim_spread'] = int(sim[g]['spread_fixed'])
        vectors.append(entry)

    # Phase 6 proved XSIM == golden; re-assert it so a stale sim_results.csv
    # can never quietly become the reference.
    disagree = [v['game_idx'] for v in vectors if 'sim_win' in v
                and (v['sim_win'] != v['exp_win'] or v['sim_spread'] != v['exp_spread'])]
    if disagree:
        raise AssertionError(f"sim_results.csv disagrees with the golden on games {disagree}")

    VEC_OUT.write_text(json.dumps({
        'generated': datetime.now().isoformat(timespec='seconds'),
        'source': 'phase6_sim_conifer/chain_golden',
        'n': len(vectors),
        'sim_checked': bool(sim),
        'vectors': vectors,
    }))
    print(f"Wrote {VEC_OUT} — {len(vectors)} vectors "
          f"({'XSIM cross-checked' if sim else 'golden only, sim_results.csv absent'})")


def main():
    b = GBDTFeatureBuilder()
    catalog = []
    skipped = Counter()

    for _, row in b.games.iterrows():
        # Only played games with complete features are useful for board inference.
        if row.get('home_win') is None or str(row.get('home_win')) == 'nan':
            skipped['unplayed'] += 1
            continue
        try:
            feats = b._encode_row(row)
        except ValueError as e:
            # Non-finite features, or a value outside ap_fixed<24,12>. The board
            # could not represent these either, so dropping them is honest.
            skipped['non-finite' if 'finite' in str(e) else 'out-of-range'] += 1
            continue

        has_score = ('home_score' in row and 'away_score' in row
                     and str(row['home_score']) != 'nan')
        catalog.append({
            'season': int(row['season']),
            'week': int(row['week']),
            'home': str(row['home_team']),
            'away': str(row['away_team']),
            'bytes': feats,
            'actual_winner': (str(row['home_team']) if int(row['home_win']) == 1
                              else str(row['away_team'])),
            'actual_spread': (int(row['home_score'] - row['away_score'])
                              if has_score else None),
        })

    catalog.sort(key=lambda g: (g['season'], g['week'], g['home']))
    payload = {
        'generated': datetime.now().isoformat(timespec='seconds'),
        'model': 'gbdt',
        'feature_count': 21,
        'protocol': ('AA + 63 raw fixed-point bytes + xor-checksum -> '
                     '55 + win_prob[3B] + spread[3B] + status'),
        'n_games': len(catalog),
        'games': catalog,
    }
    OUT.write_text(json.dumps(payload))

    weeks = len({(g['season'], g['week']) for g in catalog})
    print(f"Wrote {OUT} — {len(catalog)} games ({weeks} season/weeks)")
    for reason, n in sorted(skipped.items()):
        print(f"  skipped {n:>5} ({reason})")

    export_validation_vectors(b)


if __name__ == '__main__':
    main()
