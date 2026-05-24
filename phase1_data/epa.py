"""
EPA (Expected Points Added) rolling features for NFL teams.

Uses nflreadpy.load_team_stats (weekly) which already aggregates EPA per team per
game, so we don't need to process raw play-by-play. The two features this module
builds are the most predictive team-quality signals in NFL analytics:

  off_epa_per_play  — offensive EPA per play (passing + rushing EPA / total plays)
  def_epa_per_play  — defensive EPA allowed per play (opponent's off EPA)

Both use the same .shift(1) + rolling(4) pattern as rolling points stats so that
game N's features only use information from games 1..N-1. Cold-start (first game
in dataset) fills with 0.0, which is the league-average EPA by construction.

FPGA note: these are pre-game features computed offline in the pipeline; they
involve no new activation functions or parameter types — they just improve the
INPUT SIGNAL the network sees. Zero hardware-design impact.
"""

import os
import pandas as pd
import numpy as np

RAW_TEAM_STATS_PATH = 'data/raw/team_stats_raw.parquet'


def _load_raw_team_stats(seasons):
    """
    Pull load_team_stats for all seasons and cache to parquet.
    On subsequent runs, loads from the parquet cache (fast, offline).
    """
    if os.path.exists(RAW_TEAM_STATS_PATH):
        print(f"Loading cached team stats from {RAW_TEAM_STATS_PATH}")
        return pd.read_parquet(RAW_TEAM_STATS_PATH)

    import nflreadpy
    print(f"Pulling team stats for {min(seasons)}–{max(seasons)} (one-time download) ...")
    frames = []
    for season in seasons:
        try:
            df = nflreadpy.load_team_stats(season)
            if hasattr(df, 'to_pandas'):
                df = df.to_pandas()
            # Regular season only
            df = df[df['season_type'] == 'REG']
            frames.append(df)
            print(f"  {season}: {len(df)} team-game rows")
        except Exception as e:
            print(f"  WARNING: skipping {season} — {e}")

    all_stats = pd.concat(frames, ignore_index=True)
    os.makedirs('data/raw', exist_ok=True)
    all_stats.to_parquet(RAW_TEAM_STATS_PATH, index=False)
    print(f"Team stats cached to {RAW_TEAM_STATS_PATH} ({len(all_stats)} rows)")
    return all_stats


def _compute_epa_per_game(team_stats_df):
    """
    From weekly team stats, compute off_epa_per_play and def_epa_per_play
    per (game_id, team) row.

    off_epa_per_play: (passing_epa + rushing_epa) / (attempts + carries)
        Positive = above-average offense.
    def_epa_per_play: opponent's off_epa_per_play for that game.
        Positive = defense allowed a lot of EPA (bad); negative = stingy defense.
    """
    df = team_stats_df[['game_id', 'season', 'week', 'team', 'opponent_team',
                          'passing_epa', 'rushing_epa',
                          'attempts', 'carries']].copy()

    df['off_plays'] = (df['attempts'].fillna(0) + df['carries'].fillna(0)).clip(lower=1)
    df['off_epa_per_play'] = (
        df['passing_epa'].fillna(0) + df['rushing_epa'].fillna(0)
    ) / df['off_plays']

    # Clamp extreme outliers (e.g. very short games, data issues): ±2 EPA/play is extreme
    df['off_epa_per_play'] = df['off_epa_per_play'].clip(-2.0, 2.0)

    # Defensive EPA = opponent's offensive EPA for the same game
    off_lookup = df[['game_id', 'team', 'off_epa_per_play']].rename(
        columns={'team': 'opp_team', 'off_epa_per_play': 'opp_off_epa_per_play'}
    )
    df = df.merge(
        off_lookup,
        left_on=['game_id', 'opponent_team'],
        right_on=['game_id', 'opp_team'],
        how='left',
    )
    df['def_epa_per_play'] = df['opp_off_epa_per_play'].fillna(0.0)
    df.drop(columns=['opp_team', 'opp_off_epa_per_play'], inplace=True)

    return df[['game_id', 'season', 'week', 'team',
               'off_epa_per_play', 'def_epa_per_play']]


def _build_rolling_epa(epa_df, window=4):
    """
    For each team, compute rolling pre-game EPA stats with shift(1) leakage guard.

    Returns a DataFrame with:
        game_id, team, rolling_off_epa, rolling_def_epa
    where game N's stats are the mean of up to `window` prior games.
    Cold start (no prior games) → 0.0 (league-average EPA by construction).
    """
    # Sort chronologically within each team
    epa_df = epa_df.sort_values(['team', 'season', 'week']).reset_index(drop=True)

    stat_rows = []
    for team, group in epa_df.groupby('team'):
        group = group.copy().reset_index(drop=True)

        # shift(1): game N sees only games 1..N-1
        off_shifted = group['off_epa_per_play'].shift(1)
        def_shifted = group['def_epa_per_play'].shift(1)

        # fillna(0) at cold start → league-average prior; min_periods=1 for early games
        rolling_off = off_shifted.fillna(0.0).rolling(window, min_periods=1).mean()
        rolling_def = def_shifted.fillna(0.0).rolling(window, min_periods=1).mean()

        group['rolling_off_epa'] = rolling_off
        group['rolling_def_epa'] = rolling_def
        stat_rows.append(group[['game_id', 'team', 'rolling_off_epa', 'rolling_def_epa']])

    return pd.concat(stat_rows, ignore_index=True)


def add_epa_rolling_stats(games_df, seasons, window=4):
    """
    Add rolling pre-game EPA features to games_df.

    Features added (appended to games_df):
        home_off_epa_per_play  — home team offense quality (higher = better)
        away_off_epa_per_play  — away team offense quality
        home_def_epa_per_play  — home team defense quality (lower = better)
        away_def_epa_per_play  — away team defense quality

    Args:
        games_df: processed games DataFrame with game_id, home_team, away_team.
        seasons:  iterable of int seasons to pull (matches games_df coverage).
        window:   rolling window size (default 4, matches rolling points stats).
    """
    raw = _load_raw_team_stats(seasons)
    epa_per_game = _compute_epa_per_game(raw)
    rolling = _build_rolling_epa(epa_per_game, window=window)

    # Rejoin as home stats
    home_epa = rolling.rename(columns={
        'team': 'home_team',
        'rolling_off_epa': 'home_off_epa_per_play',
        'rolling_def_epa': 'home_def_epa_per_play',
    })
    away_epa = rolling.rename(columns={
        'team': 'away_team',
        'rolling_off_epa': 'away_off_epa_per_play',
        'rolling_def_epa': 'away_def_epa_per_play',
    })

    games_df = games_df.merge(
        home_epa[['game_id', 'home_team', 'home_off_epa_per_play', 'home_def_epa_per_play']],
        on=['game_id', 'home_team'], how='left',
    )
    games_df = games_df.merge(
        away_epa[['game_id', 'away_team', 'away_off_epa_per_play', 'away_def_epa_per_play']],
        on=['game_id', 'away_team'], how='left',
    )

    # Any unmatched games (game_id not in team_stats, very early seasons) → 0.0 (neutral prior)
    for col in ['home_off_epa_per_play', 'away_off_epa_per_play',
                'home_def_epa_per_play', 'away_def_epa_per_play']:
        games_df[col] = games_df[col].fillna(0.0)

    print(f"EPA features added. off_epa range: "
          f"[{games_df['home_off_epa_per_play'].min():.3f}, "
          f"{games_df['home_off_epa_per_play'].max():.3f}]")

    return games_df
