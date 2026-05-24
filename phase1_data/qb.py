"""
QB rolling features for NFL game prediction.

Builds pre-game rolling QB quality stats using nflreadpy.load_player_stats
(per-player, per-game, weekly data). QB is the highest-variance position in
football and a key driver of results not fully captured by team-level stats.

Features produced (4 total):
  home_qb_epa_per_drop  — home QB's rolling EPA per dropback (last 4 games)
  away_qb_epa_per_drop  — away QB's rolling EPA per dropback
  home_qb_cpoe          — home QB's rolling CPOE (completion % over expected)
  away_qb_cpoe          — away QB's rolling CPOE

Why these two metrics:
  epa_per_drop — the standard for QB efficiency; adjusts for situation (down/
                 distance/field position) unlike raw yards or passer rating.
  cpoe         — completion % vs. model expectation given air yards/coverage;
                 best single metric for separating skill from circumstance.
                 Null for pre-2016 (NGS data) → filled with 0.0 (league average).

All stats use .shift(1) + rolling(4) so game N only sees games 1..N-1.
Cold start (no prior games) → 0.0 for both metrics (league-average prior).

FPGA note: QB names/IDs are resolved entirely offline in the pipeline — at
inference time the laptop only needs to look up the QB's rolling stats from
the last 4 games (same as any rolling stat). Zero on-chip impact.
"""

import os
import pandas as pd
import numpy as np

RAW_QB_STATS_PATH = 'data/raw/qb_stats_raw.parquet'

MIN_ATTEMPTS = 5   # only count games where QB had >= 5 attempts in rolling history
                    # prevents garbage-time 1-attempt appearances from polluting rolling avg


def _load_raw_qb_stats(seasons):
    """Pull load_player_stats for all seasons and cache. Loads from cache on repeat runs."""
    if os.path.exists(RAW_QB_STATS_PATH):
        print(f"Loading cached QB stats from {RAW_QB_STATS_PATH}")
        return pd.read_parquet(RAW_QB_STATS_PATH)

    import nflreadpy
    print(f"Pulling player stats for {min(seasons)}–{max(seasons)} (one-time download) ...")
    frames = []
    for season in seasons:
        try:
            df = nflreadpy.load_player_stats(season)
            if hasattr(df, 'to_pandas'):
                df = df.to_pandas()
            df = df[df['season_type'] == 'REG']
            frames.append(df)
            print(f"  {season}: {len(df)} player-game rows")
        except Exception as e:
            print(f"  WARNING: skipping {season} — {e}")

    all_stats = pd.concat(frames, ignore_index=True)
    os.makedirs('data/raw', exist_ok=True)
    all_stats.to_parquet(RAW_QB_STATS_PATH, index=False)
    print(f"QB/player stats cached to {RAW_QB_STATS_PATH} ({len(all_stats)} rows)")
    return all_stats


def _compute_qb_per_game(player_stats_df):
    """
    Filter to QB rows and compute per-game efficiency metrics.

    Returns a DataFrame with [team, player_id, season, week,
                               attempts, epa_per_drop, cpoe] per QB per game.
    game_id is NaN for pre-~2016 seasons, so we join on (season, week, team) instead.
    """
    # Keep QBs with any passing attempts
    qb = player_stats_df[
        player_stats_df['attempts'].fillna(0) > 0
    ][['player_id', 'season', 'week', 'team',
       'attempts', 'passing_epa', 'passing_cpoe']].copy()

    # Normalize JAC → JAX (nflreadpy inconsistency for pre-2021 Jacksonville data)
    qb['team'] = qb['team'].replace('JAC', 'JAX')

    # EPA per dropback — normalise by attempts
    qb['attempts'] = qb['attempts'].fillna(0)
    qb['passing_epa'] = qb['passing_epa'].fillna(0.0)
    qb['epa_per_drop'] = qb['passing_epa'] / qb['attempts'].clip(lower=1)

    # Clamp extreme outliers (very short games / data errors)
    qb['epa_per_drop'] = qb['epa_per_drop'].clip(-3.0, 3.0)

    # CPOE: available from ~2016 onward via NGS; null before → 0.0 (league average)
    qb['cpoe'] = qb['passing_cpoe'].fillna(0.0).clip(-30.0, 30.0)

    # Drop duplicates: take first appearance if a player has multiple rows in a week
    qb = qb.sort_values(['player_id', 'season', 'week', 'attempts'], ascending=[True, True, True, False])
    qb = qb.drop_duplicates(subset=['player_id', 'season', 'week'], keep='first')

    return qb[['player_id', 'season', 'week', 'team',
               'attempts', 'epa_per_drop', 'cpoe']]


def _build_rolling_qb(qb_df, window=4):
    """
    For each QB, compute rolling pre-game EPA/drop and CPOE with shift(1) guard.

    Only games with attempts >= MIN_ATTEMPTS count toward the rolling average —
    garbage-time 1-attempt appearances should not pollute a starter's history.

    Returns DataFrame [player_id, season, week, team, rolling_epa, rolling_cpoe].
    Joined to games on (season, week, team, player_id) — game_id is unreliable pre-2016.
    """
    # Sort chronologically per player
    qb_df = qb_df.sort_values(['player_id', 'season', 'week']).reset_index(drop=True)

    stat_rows = []
    for player_id, group in qb_df.groupby('player_id'):
        group = group.copy().reset_index(drop=True)

        # Mask low-attempt games — treat them as missing for rolling purposes
        epa = group['epa_per_drop'].where(group['attempts'] >= MIN_ATTEMPTS)
        cpoe = group['cpoe'].where(group['attempts'] >= MIN_ATTEMPTS)

        # shift(1): pre-game rolling uses only prior games
        epa_shifted = epa.shift(1).fillna(0.0)
        cpoe_shifted = cpoe.shift(1).fillna(0.0)

        rolling_epa = epa_shifted.rolling(window, min_periods=1).mean()
        rolling_cpoe = cpoe_shifted.rolling(window, min_periods=1).mean()

        group['rolling_epa'] = rolling_epa
        group['rolling_cpoe'] = rolling_cpoe
        stat_rows.append(group[['player_id', 'season', 'week', 'team',
                                 'rolling_epa', 'rolling_cpoe']])

    return pd.concat(stat_rows, ignore_index=True)


def add_qb_rolling_stats(games_df, seasons, window=4):
    """
    Add rolling pre-game QB efficiency features to games_df.

    Features added:
        home_qb_epa_per_drop  — home QB rolling EPA per dropback (higher = better)
        away_qb_epa_per_drop  — away QB rolling EPA per dropback
        home_qb_cpoe          — home QB rolling CPOE (higher = better)
        away_qb_cpoe          — away QB rolling CPOE

    Requires games_df to have columns: game_id, home_qb_id, away_qb_id.
    Games where qb_id is null or QB hasn't played yet → 0.0 (league-average prior).
    """
    if 'home_qb_id' not in games_df.columns:
        print("WARNING: home_qb_id not in games_df — QB features will be all 0.0")
        for col in ['home_qb_epa_per_drop','away_qb_epa_per_drop',
                    'home_qb_cpoe','away_qb_cpoe']:
            games_df[col] = 0.0
        return games_df

    raw = _load_raw_qb_stats(seasons)
    qb_per_game = _compute_qb_per_game(raw)
    rolling = _build_rolling_qb(qb_per_game, window=window)

    # Join home QB stats on (season, week, team, player_id)
    # game_id is NaN for pre-~2016 seasons in load_player_stats, so we use team+week instead
    home_rolling = rolling.rename(columns={
        'player_id': 'home_qb_id',
        'team': 'home_team',
        'rolling_epa': 'home_qb_epa_per_drop',
        'rolling_cpoe': 'home_qb_cpoe',
    })
    games_df = games_df.merge(
        home_rolling[['season', 'week', 'home_team', 'home_qb_id',
                      'home_qb_epa_per_drop', 'home_qb_cpoe']],
        on=['season', 'week', 'home_team', 'home_qb_id'], how='left',
    )

    # Join away QB stats on (season, week, team, player_id)
    away_rolling = rolling.rename(columns={
        'player_id': 'away_qb_id',
        'team': 'away_team',
        'rolling_epa': 'away_qb_epa_per_drop',
        'rolling_cpoe': 'away_qb_cpoe',
    })
    games_df = games_df.merge(
        away_rolling[['season', 'week', 'away_team', 'away_qb_id',
                      'away_qb_epa_per_drop', 'away_qb_cpoe']],
        on=['season', 'week', 'away_team', 'away_qb_id'], how='left',
    )

    # Any unmatched games → 0.0 (league-average prior)
    for col in ['home_qb_epa_per_drop', 'away_qb_epa_per_drop',
                'home_qb_cpoe', 'away_qb_cpoe']:
        games_df[col] = games_df[col].fillna(0.0)

    matched = games_df['home_qb_epa_per_drop'].ne(0.0).mean()
    print(f"QB features added. {matched:.1%} of games have non-zero home QB rolling EPA. "
          f"EPA range: [{games_df['home_qb_epa_per_drop'].min():.3f}, "
          f"{games_df['home_qb_epa_per_drop'].max():.3f}]")

    return games_df
