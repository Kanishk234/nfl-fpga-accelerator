"""
Phase 1 Data Pipeline — main entry point.

Pulls NFL schedule data via nflreadpy, computes Elo ratings, rolling team
stats, and game context features, then validates and saves the processed
dataset and feature manifest.

Run from the project root:
    python phase1_data/pipeline.py
"""

import os
import sys

import pandas as pd

# Allow running from the project root without installing the package
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from phase1_data.elo import calculate_elo_ratings, validate_elo
from phase1_data.features import add_rolling_stats, add_game_context
from phase1_data.validate import (
    CANONICAL_FEATURES,
    LABEL_COLUMNS,
    validate_and_lock_features,
    save_artifacts,
)

# Seasons to pull — 2000-2024
SEASONS = range(2000, 2025)

RAW_PATH = 'data/raw/schedules_raw.parquet'


def pull_raw_data():
    """Pull raw NFL schedules from nflreadpy and cache to parquet."""
    import nflreadpy

    print(f"Pulling schedules for seasons {min(SEASONS)}–{max(SEASONS)} ...")

    season_frames = []
    for season in SEASONS:
        try:
            df = nflreadpy.load_schedules(season)
            if hasattr(df, 'to_pandas'):
                df = df.to_pandas()
            season_frames.append(df)
            print(f"  Season {season}: {len(df)} games")
        except Exception as e:
            print(f"  WARNING: skipping season {season} — {e}")

    if not season_frames:
        raise RuntimeError("No seasons loaded — check nflreadpy installation")

    schedules = pd.concat(season_frames, ignore_index=True)

    os.makedirs('data/raw', exist_ok=True)
    schedules.to_parquet(RAW_PATH, index=False)
    print(f"Raw data saved to {RAW_PATH} ({len(schedules)} total rows)")

    return schedules


def load_and_filter(schedules):
    """
    Filter raw schedules to completed regular-season games with all required columns.

    Returns a sorted DataFrame with base labels (home_win, spread) added.
    """
    # Regular season only — playoffs are a different statistical regime
    games = schedules[schedules['game_type'] == 'REG'].copy()

    # Completed games only — future scheduled games have no scores
    games = games.dropna(subset=['home_score', 'away_score'])

    # Keep only the columns we need downstream
    keep_cols = [
        'game_id', 'season', 'week', 'home_team', 'away_team',
        'home_score', 'away_score', 'location', 'roof',
        'spread_line', 'total_line', 'gameday',
    ]
    # Only keep columns that actually exist in the dataset
    keep_cols = [c for c in keep_cols if c in games.columns]
    games = games[keep_cols].copy()

    # Ensure correct types
    games['home_score'] = games['home_score'].astype(float)
    games['away_score'] = games['away_score'].astype(float)
    games['season'] = games['season'].astype(int)
    games['week'] = games['week'].astype(int)

    # Sort chronologically — CRITICAL for Elo and rolling stats to be correct
    games = games.sort_values(['season', 'week']).reset_index(drop=True)

    # Derive labels
    games['home_win'] = (games['home_score'] > games['away_score']).astype(int)
    games['spread'] = games['home_score'] - games['away_score']

    assert games['home_win'].isnull().sum() == 0, "Nulls in home_win"
    assert games['spread'].isnull().sum() == 0, "Nulls in spread"

    return games


if __name__ == '__main__':
    print("=== Phase 1: Data Pipeline ===")

    # 1. Pull raw data
    schedules = pull_raw_data()
    games = load_and_filter(schedules)
    print(f"Loaded {len(games)} completed regular season games")

    # 2. Elo ratings
    games = calculate_elo_ratings(games)
    validate_elo(games)
    print("Elo ratings calculated and validated")

    # 3. Rolling features
    games = add_rolling_stats(games, window=4)
    games = add_game_context(games)
    print("Rolling features and context added")

    # 4. Drop rows where any canonical feature or label is null
    # min_periods=1 means we do get values for early games, but we still
    # drop any remaining nulls (e.g. game_date gaps producing NaN rest_days)
    before_drop = len(games)
    games = games.dropna(subset=CANONICAL_FEATURES + LABEL_COLUMNS)
    print(f"After dropping insufficient history rows: {len(games)} games "
          f"(dropped {before_drop - len(games)})")

    # 5. Validate and save
    validate_and_lock_features(games)
    save_artifacts(games)

    print("=== Phase 1 Complete ===")
