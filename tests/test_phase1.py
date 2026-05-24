"""
Phase 1 test suite.

Tests cover: Elo correctness, rolling feature integrity, leakage guards,
feature count, null checks, distribution sanity, temporal ordering,
and artifact output.
"""

import json
import os

import numpy as np
import pandas as pd
import pytest

from phase1_data.elo import (
    BASE_ELO,
    HOME_ADVANTAGE,
    K,
    SEASON_REVERSION,
    calculate_elo_ratings,
)
from phase1_data.features import add_game_context, add_rolling_stats
from phase1_data.validate import CANONICAL_FEATURES, LABEL_COLUMNS


# ---------------------------------------------------------------------------
# Helpers — build minimal synthetic game DataFrames
# ---------------------------------------------------------------------------

def _make_games(records):
    """
    Build a minimal games DataFrame from a list of dicts.
    Provides defaults for all columns expected by the pipeline functions.
    """
    defaults = {
        'game_id': None,
        'season': 2020,
        'week': 1,
        'home_team': 'KC',
        'away_team': 'LV',
        'home_score': 24.0,
        'away_score': 17.0,
        'location': 'Home',
        'roof': 'outdoors',
        'spread_line': -3.0,
        'total_line': 47.0,
        'gameday': '2020-09-10',
    }
    rows = []
    for i, rec in enumerate(records):
        row = {**defaults, **rec}
        if row['game_id'] is None:
            row['game_id'] = f"game_{i:04d}"
        rows.append(row)

    df = pd.DataFrame(rows)
    df['gameday'] = pd.to_datetime(df['gameday'])
    df['home_win'] = (df['home_score'] > df['away_score']).astype(int)
    df['spread'] = df['home_score'] - df['away_score']
    df = df.sort_values(['season', 'week']).reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# Elo tests
# ---------------------------------------------------------------------------

class TestEloInitialValues:
    def test_all_teams_start_at_base_elo(self):
        """Teams with no history should start at BASE_ELO=1500."""
        games = _make_games([
            {'home_team': 'KC', 'away_team': 'LV', 'season': 2020, 'week': 1,
             'home_score': 30.0, 'away_score': 20.0},
        ])
        result = calculate_elo_ratings(games)
        # Pre-game Elos for the very first game must be BASE_ELO
        assert result.iloc[0]['home_elo'] == BASE_ELO
        assert result.iloc[0]['away_elo'] == BASE_ELO


class TestEloNoFutureLeakage:
    def test_pre_game_elo_equals_post_game4_elo(self):
        """
        Game 5's stored Elo must equal what was computed AFTER game 4.
        Construct 5 sequential games for the same home team (KC) and
        verify that game 5's home_elo matches the manually tracked value.
        """
        games = _make_games([
            {'home_team': 'KC', 'away_team': 'A', 'season': 2020, 'week': w,
             'home_score': 30.0, 'away_score': 20.0}
            for w in range(1, 6)
        ])
        result = calculate_elo_ratings(games)

        # Manually simulate Elo for both KC and A through 4 games
        # 'A' also gets updated after each game — can't assume it stays at BASE_ELO
        kc_elo = BASE_ELO
        a_elo = BASE_ELO
        for i in range(4):
            elo_diff = kc_elo + HOME_ADVANTAGE - a_elo
            expected_home = 1.0 / (1.0 + 10.0 ** (-elo_diff / 400.0))
            kc_elo += K * (1.0 - expected_home)        # KC wins every game
            a_elo  += K * (0.0 - (1.0 - expected_home))  # A loses every game

        # Game 5 (index 4) should store KC's Elo AFTER games 1–4
        stored_game5_elo = result.iloc[4]['home_elo']
        assert abs(stored_game5_elo - kc_elo) < 0.01, (
            f"Game 5 stored Elo {stored_game5_elo:.2f} != expected {kc_elo:.2f}"
        )


class TestEloBounds:
    def test_elo_within_bounds_on_full_dataset(self):
        """After processing all games, every Elo must be in [1000, 2200]."""
        parquet_path = 'data/processed/games.parquet'
        if not os.path.exists(parquet_path):
            pytest.skip("games.parquet not yet generated — run pipeline first")
        games = pd.read_parquet(parquet_path)
        assert games['home_elo'].between(1000, 2200).all(), \
            f"home_elo out of bounds: min={games['home_elo'].min():.0f} max={games['home_elo'].max():.0f}"
        assert games['away_elo'].between(1000, 2200).all(), \
            f"away_elo out of bounds: min={games['away_elo'].min():.0f} max={games['away_elo'].max():.0f}"


# ---------------------------------------------------------------------------
# Rolling feature tests
# ---------------------------------------------------------------------------

class TestRollingNoLeakage:
    def test_game_n_rolling_excludes_game_n_score(self):
        """
        For a single team (KC as home), game N's rolling_pts_scored_avg
        must NOT include game N's own score.

        Synthetic sequence with distinct scores makes this verifiable.
        """
        scores = [10.0, 20.0, 30.0, 40.0, 50.0]
        games = _make_games([
            {'home_team': 'KC', 'away_team': 'OPP', 'season': 2020, 'week': w + 1,
             'home_score': scores[w], 'away_score': 0.0}
            for w in range(5)
        ])
        result = add_rolling_stats(games, window=4)

        # Game 1 (index 0): no prior games → rolling avg should be NaN (or 0 from min_periods)
        # Game 2 (index 1): prior game had score=10 → avg should be 10
        # Game 3 (index 2): prior games had scores 10, 20 → avg should be 15
        # Game 5 (index 4): prior 4 games had 10,20,30,40 → avg should be 25
        g2_avg = result.iloc[1]['home_pts_scored_avg']
        assert abs(g2_avg - 10.0) < 0.01, \
            f"Game 2 avg should be 10.0 (only game 1's score), got {g2_avg}"

        g3_avg = result.iloc[2]['home_pts_scored_avg']
        assert abs(g3_avg - 15.0) < 0.01, \
            f"Game 3 avg should be 15.0 (mean of 10,20), got {g3_avg}"

        g5_avg = result.iloc[4]['home_pts_scored_avg']
        assert abs(g5_avg - 25.0) < 0.01, \
            f"Game 5 avg should be 25.0 (mean of 10,20,30,40), got {g5_avg}"


class TestRollingWindow:
    def test_window_of_4_uses_correct_prior_games(self):
        """
        With window=4:
          - Game 5's avg = mean of games 1–4 (exactly 4 games)
          - Game 3's avg = mean of games 1–2 (min_periods=1)
        """
        scores = [10.0, 20.0, 30.0, 40.0, 50.0]
        games = _make_games([
            {'home_team': 'KC', 'away_team': 'OPP', 'season': 2020, 'week': w + 1,
             'home_score': scores[w], 'away_score': 0.0}
            for w in range(5)
        ])
        result = add_rolling_stats(games, window=4)

        # Game 5: mean of [10, 20, 30, 40] = 25
        assert abs(result.iloc[4]['home_pts_scored_avg'] - 25.0) < 0.01

        # Game 3: mean of [10, 20] = 15  (only 2 prior games exist)
        assert abs(result.iloc[2]['home_pts_scored_avg'] - 15.0) < 0.01


# ---------------------------------------------------------------------------
# Processed dataset tests (require pipeline to have run)
# ---------------------------------------------------------------------------

@pytest.fixture(scope='module')
def games_df():
    parquet_path = 'data/processed/games.parquet'
    if not os.path.exists(parquet_path):
        pytest.skip("games.parquet not yet generated — run pipeline first")
    return pd.read_parquet(parquet_path)


class TestFeatureCount:
    def test_exactly_21_feature_columns(self, games_df):
        """Output DataFrame must contain exactly the 21 canonical feature columns."""
        for feature in CANONICAL_FEATURES:
            assert feature in games_df.columns, f"Missing feature: {feature}"
        assert len(CANONICAL_FEATURES) == 21, \
            f"CANONICAL_FEATURES has {len(CANONICAL_FEATURES)} entries, expected 21"


class TestNoNulls:
    def test_zero_nulls_in_features_and_labels(self, games_df):
        """After pipeline completes, every feature and label must be non-null."""
        for col in CANONICAL_FEATURES + LABEL_COLUMNS:
            null_count = games_df[col].isnull().sum()
            assert null_count == 0, f"{col} has {null_count} nulls"


class TestHomeWinRate:
    def test_home_win_rate_in_expected_range(self, games_df):
        """
        Historical NFL home win rate is ~54–60%.
        Outside this range implies a filtering or label bug.
        """
        rate = games_df['home_win'].mean()
        assert 0.54 <= rate <= 0.60, \
            f"Home win rate {rate:.3f} outside expected [0.54, 0.60]"


class TestSpreadDistribution:
    def test_spread_mean_positive(self, games_df):
        """Home teams score more on average → spread mean should be positive."""
        assert games_df['spread'].mean() > 0, \
            f"Spread mean {games_df['spread'].mean():.2f} should be positive"

    def test_spread_std_in_expected_range(self, games_df):
        """NFL game margins have std roughly 10–20 points."""
        std = games_df['spread'].std()
        assert 10 <= std <= 20, \
            f"Spread std {std:.2f} outside expected [10, 20]"


class TestTemporalOrdering:
    def test_games_sorted_by_season_week(self, games_df):
        """
        Dataset must be sorted chronologically.
        No game at index i should have a later (season, week) than game i+1.
        """
        seasons = games_df['season'].values
        weeks = games_df['week'].values

        for i in range(len(seasons) - 1):
            current = (seasons[i], weeks[i])
            nxt = (seasons[i + 1], weeks[i + 1])
            assert current <= nxt, (
                f"Temporal ordering violated at index {i}: "
                f"{current} > {nxt}"
            )


class TestFeaturesJson:
    def test_features_json_exists(self):
        assert os.path.exists('artifacts/features.json'), \
            "artifacts/features.json does not exist — run pipeline first"

    def test_features_json_has_correct_structure(self):
        with open('artifacts/features.json') as f:
            meta = json.load(f)
        assert 'features' in meta, "features.json missing 'features' key"
        assert len(meta['features']) == 21, \
            f"features.json has {len(meta['features'])} features, expected 21"

    def test_features_json_order_matches_canonical(self):
        with open('artifacts/features.json') as f:
            meta = json.load(f)
        assert meta['features'] == CANONICAL_FEATURES, \
            "features.json order does not match CANONICAL_FEATURES"
