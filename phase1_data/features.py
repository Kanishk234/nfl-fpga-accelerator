"""
Rolling feature engineering for NFL game data.

All rolling stats use .shift(1) so that game N's features only include
information from games 1..N-1. This is the critical leakage guard.
"""

import pandas as pd
import numpy as np


def _compute_win_streak(win_series_shifted):
    """
    Compute signed win streak from a shifted win indicator series (0 or 1).

    Positive = current win streak, negative = current loss streak.
    The series must already be shifted so it represents prior game outcomes.
    """
    streaks = []
    current_streak = 0
    for val in win_series_shifted:
        if pd.isna(val):
            # No prior game — streak is 0
            streaks.append(0)
            current_streak = 0
        elif val == 1:
            current_streak = max(1, current_streak + 1)
            streaks.append(current_streak)
        else:
            current_streak = min(-1, current_streak - 1)
            streaks.append(current_streak)
    return streaks


def add_rolling_stats(games_df, window=4):
    """
    Add rolling per-team statistics (last `window` games) to games_df.

    For each game each team appears as either the home or away team.
    We build a unified per-team game log, compute rolling stats with
    shift(1), then rejoin back onto games_df.

    Returns games_df with new columns:
        home_pts_scored_avg, away_pts_scored_avg
        home_pts_allowed_avg, away_pts_allowed_avg
        home_point_diff_avg, away_point_diff_avg
        home_win_streak, away_win_streak
        home_rest_days, away_rest_days
    """
    # Build per-team view — each game contributes one home row and one away row
    home_view = games_df[['game_id', 'season', 'week', 'gameday',
                           'home_team', 'home_score', 'away_score']].copy()
    home_view = home_view.rename(columns={
        'home_team': 'team',
        'home_score': 'pts_scored',
        'away_score': 'pts_allowed',
    })
    home_view['is_home'] = 1

    away_view = games_df[['game_id', 'season', 'week', 'gameday',
                           'away_team', 'away_score', 'home_score']].copy()
    away_view = away_view.rename(columns={
        'away_team': 'team',
        'away_score': 'pts_scored',
        'home_score': 'pts_allowed',
    })
    away_view['is_home'] = 0

    all_games = pd.concat([home_view, away_view], ignore_index=True)
    all_games = all_games.sort_values(['team', 'season', 'week']).reset_index(drop=True)

    # Ensure gameday is datetime for diff computation
    all_games['gameday'] = pd.to_datetime(all_games['gameday'])

    stat_rows = []

    for team, group in all_games.groupby('team'):
        group = group.copy().reset_index(drop=True)

        # shift(1) so game N's stats are computed from games 1..N-1
        pts_scored_shifted = group['pts_scored'].shift(1)
        pts_allowed_shifted = group['pts_allowed'].shift(1)
        point_diff_shifted = (group['pts_scored'] - group['pts_allowed']).shift(1)

        rolling_scored = pts_scored_shifted.rolling(window, min_periods=1).mean()
        rolling_allowed = pts_allowed_shifted.rolling(window, min_periods=1).mean()
        rolling_diff = point_diff_shifted.rolling(window, min_periods=1).mean()

        # Win indicator for prior games (shifted)
        win_indicator = (group['pts_scored'] > group['pts_allowed']).astype(float)
        win_shifted = win_indicator.shift(1)
        streak = _compute_win_streak(win_shifted)

        # Rest days: days since the team's previous game
        # shift(1) means we're looking at how long since the game before this one
        days_since_last = group['gameday'].diff().dt.days
        days_since_last = days_since_last.fillna(7)  # first game of season = standard week
        rest_days = days_since_last  # no additional shift — diff() already uses prior game's date

        group['rolling_pts_scored'] = rolling_scored
        group['rolling_pts_allowed'] = rolling_allowed
        group['rolling_point_diff'] = rolling_diff
        group['win_streak'] = streak
        group['rest_days'] = rest_days

        stat_rows.append(group)

    all_games_stats = pd.concat(stat_rows, ignore_index=True)

    # Rejoin home stats back onto games_df
    home_stats = all_games_stats[all_games_stats['is_home'] == 1][[
        'game_id', 'rolling_pts_scored', 'rolling_pts_allowed',
        'rolling_point_diff', 'win_streak', 'rest_days'
    ]].rename(columns={
        'rolling_pts_scored': 'home_pts_scored_avg',
        'rolling_pts_allowed': 'home_pts_allowed_avg',
        'rolling_point_diff': 'home_point_diff_avg',
        'win_streak': 'home_win_streak',
        'rest_days': 'home_rest_days',
    })

    away_stats = all_games_stats[all_games_stats['is_home'] == 0][[
        'game_id', 'rolling_pts_scored', 'rolling_pts_allowed',
        'rolling_point_diff', 'win_streak', 'rest_days'
    ]].rename(columns={
        'rolling_pts_scored': 'away_pts_scored_avg',
        'rolling_pts_allowed': 'away_pts_allowed_avg',
        'rolling_point_diff': 'away_point_diff_avg',
        'win_streak': 'away_win_streak',
        'rest_days': 'away_rest_days',
    })

    games_df = games_df.merge(home_stats, on='game_id', how='left')
    games_df = games_df.merge(away_stats, on='game_id', how='left')

    return games_df


def add_game_context(games_df):
    """
    Add game-level context features that don't require per-team history.

    Features added:
        is_dome        — 1 if game played in dome/closed roof
        season_progress — week / 18.0, float in [0,1]
        vegas_spread   — opening Vegas spread line (home-favored = negative)
    """
    games_df = games_df.copy()

    # Dome/closed roof proxy for weather neutrality
    games_df['is_dome'] = games_df['roof'].isin(['dome', 'closed']).astype(int)

    # Season progress — captures early vs. late season dynamics
    games_df['season_progress'] = games_df['week'] / 18.0

    # Vegas opening spread — strongest single predictor available
    # Using spread_line (opening) not closing line; fill missing with 0 (neutral prior)
    games_df['vegas_spread'] = games_df['spread_line'].fillna(0.0)

    return games_df
