"""
Feature validation and artifact saving for Phase 1.

CANONICAL_FEATURES is the permanent, ordered feature list.
Feature count (17) and order are fixed after Phase 4 FPGA synthesis.
The FPGA expects byte 0 = feature 0, byte 1 = feature 1, etc.
Modifying this list after synthesis requires full re-synthesis.
"""

import json
import os

# 21 features — fixed forever once FPGA synthesis begins (Phase 4)
# Order is sacred: FPGA input byte N = CANONICAL_FEATURES[N]
# Added in v2: temp, wind, is_div_game (weather + divisional context)
# Added in v3: vegas_total (expected scoring environment, orthogonal to spread)
# v4 candidate (QB rolling EPA/CPOE) tested and reverted — pre-2016 cold-start zeros
#   injected noise that hurt AUC by ~2× noise band; net neutral/negative.
# New features are always appended so existing byte offsets never shift.
CANONICAL_FEATURES = [
    'home_elo',
    'away_elo',
    'elo_diff',
    'home_rest_days',
    'away_rest_days',
    'home_win_streak',
    'away_win_streak',
    'home_pts_scored_avg',
    'away_pts_scored_avg',
    'home_pts_allowed_avg',
    'away_pts_allowed_avg',
    'home_point_diff_avg',
    'away_point_diff_avg',
    'is_dome',
    'week',
    'season_progress',
    'vegas_spread',
    'temp',
    'wind',
    'is_div_game',
    'vegas_total',
]

LABEL_COLUMNS = ['home_win', 'spread']


def validate_and_lock_features(games_df):
    """
    Assert all features and labels are present, non-null, and in valid ranges.
    Prints a summary to stdout for the pipeline progress log.
    """
    # All canonical features must be present
    for feature in CANONICAL_FEATURES:
        assert feature in games_df.columns, f"Missing feature: {feature}"

    # No nulls in features or labels
    for col in CANONICAL_FEATURES + LABEL_COLUMNS:
        null_count = games_df[col].isnull().sum()
        assert null_count == 0, f"{col} has {null_count} nulls"

    # Value range sanity checks
    assert games_df['home_elo'].between(1000, 2200).all(), \
        f"home_elo out of range: [{games_df['home_elo'].min():.0f}, {games_df['home_elo'].max():.0f}]"
    assert games_df['away_elo'].between(1000, 2200).all(), \
        f"away_elo out of range: [{games_df['away_elo'].min():.0f}, {games_df['away_elo'].max():.0f}]"
    assert games_df['home_win'].isin([0, 1]).all(), \
        "home_win contains values other than 0 and 1"
    assert games_df['week'].between(1, 22).all(), \
        f"week out of range: [{games_df['week'].min()}, {games_df['week'].max()}]"
    assert games_df['season_progress'].between(0, 1).all(), \
        f"season_progress out of range: [{games_df['season_progress'].min():.3f}, {games_df['season_progress'].max():.3f}]"
    assert games_df['is_dome'].isin([0, 1]).all(), \
        "is_dome contains values other than 0 and 1"

    # Confirm feature count has not drifted
    assert len(CANONICAL_FEATURES) == 21, \
        f"Feature count changed to {len(CANONICAL_FEATURES)} — re-check FPGA implications"

    print(f"Validation passed: {len(games_df)} games, {len(CANONICAL_FEATURES)} features")
    print(f"Seasons: {games_df['season'].min()} — {games_df['season'].max()}")
    print(f"Home win rate: {games_df['home_win'].mean():.3f} (expect ~0.57)")
    print(f"Spread mean: {games_df['spread'].mean():.2f}, std: {games_df['spread'].std():.2f}")


def save_artifacts(games_df):
    """
    Save processed games to parquet and write the locked feature manifest.

    artifacts/features.json must not be modified after Phase 4 synthesis.
    """
    os.makedirs('data/processed', exist_ok=True)
    os.makedirs('artifacts', exist_ok=True)

    # Save processed dataset
    games_df.to_parquet('data/processed/games.parquet', index=False)

    # Save locked feature manifest — order is permanent
    manifest = {
        'features': CANONICAL_FEATURES,
        'labels': LABEL_COLUMNS,
        'feature_count': len(CANONICAL_FEATURES),
        'created_from_seasons': f"{games_df['season'].min()}-{games_df['season'].max()}",
    }
    with open('artifacts/features.json', 'w') as f:
        json.dump(manifest, f, indent=2)

    print("Saved: data/processed/games.parquet")
    print("Saved: artifacts/features.json")
    print("WARNING: Never modify features.json after FPGA synthesis")
