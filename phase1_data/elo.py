"""
Elo rating engine for NFL teams.

Constants are calibrated to match FiveThirtyEight's NFL Elo model.
Pre-game Elo is stored as a feature — never post-game — to avoid
data leakage at inference time.
"""

# Standard NFL Elo constants (FiveThirtyEight calibration)
K = 20               # update speed: too high = volatile, too low = slow to adapt
HOME_ADVANTAGE = 48  # elo points added to home expected score (~3-pt spread advantage)
BASE_ELO = 1500      # starting rating for all teams
SEASON_REVERSION = 0.33  # pull 1/3 of the way back toward 1500 each new season
                         # prevents old dominance carrying forward too strongly


def calculate_elo_ratings(games_df):
    """
    Compute pre-game Elo ratings for every game in chronological order.

    games_df must already be sorted by (season, week) ascending.
    Returns games_df with new columns: home_elo, away_elo, elo_diff.
    """
    # Collect all unique teams across the dataset
    all_teams = set(games_df['home_team'].unique()) | set(games_df['away_team'].unique())
    team_elo = {team: BASE_ELO for team in all_teams}

    prev_season = None
    pre_game_elo_home = []
    pre_game_elo_away = []

    for _, row in games_df.iterrows():
        season = row['season']

        # Season reversion — partially reset toward mean at the start of each new season
        if prev_season is not None and season != prev_season:
            for team in team_elo:
                team_elo[team] = team_elo[team] + SEASON_REVERSION * (BASE_ELO - team_elo[team])

        prev_season = season

        home = row['home_team']
        away = row['away_team']

        # Store PRE-GAME elo — at inference time for a future game we only have pre-game Elo
        pre_game_elo_home.append(team_elo[home])
        pre_game_elo_away.append(team_elo[away])

        # Expected win probability using the standard logistic Elo formula
        elo_diff = team_elo[home] + HOME_ADVANTAGE - team_elo[away]
        expected_home = 1.0 / (1.0 + 10.0 ** (-elo_diff / 400.0))

        # Actual result
        home_score = row['home_score']
        away_score = row['away_score']
        if home_score > away_score:
            actual = 1.0
        elif home_score == away_score:
            actual = 0.5
        else:
            actual = 0.0

        # Update Elos AFTER storing pre-game values
        team_elo[home] += K * (actual - expected_home)
        team_elo[away] += K * ((1.0 - actual) - (1.0 - expected_home))

    games_df = games_df.copy()
    games_df['home_elo'] = pre_game_elo_home
    games_df['away_elo'] = pre_game_elo_away
    games_df['elo_diff'] = games_df['home_elo'] - games_df['away_elo']

    return games_df


def validate_elo(games_df):
    """Sanity checks on computed Elo values."""
    assert games_df['home_elo'].between(1000, 2200).all(), \
        f"home_elo out of bounds: min={games_df['home_elo'].min():.0f}, max={games_df['home_elo'].max():.0f}"
    assert games_df['away_elo'].between(1000, 2200).all(), \
        f"away_elo out of bounds: min={games_df['away_elo'].min():.0f}, max={games_df['away_elo'].max():.0f}"
    assert games_df[['home_elo', 'away_elo']].isnull().sum().sum() == 0, \
        "Nulls found in elo columns"

    import numpy as np
    assert np.allclose(
        games_df['elo_diff'].values,
        (games_df['home_elo'] - games_df['away_elo']).values
    ), "elo_diff does not equal home_elo - away_elo"

    # Print current-season Elo ratings as a sanity check
    latest_season = games_df['season'].max()
    latest_games = games_df[games_df['season'] == latest_season]

    home_elos = latest_games.set_index('home_team')['home_elo']
    away_elos = latest_games.set_index('away_team')['away_elo']
    all_elos = (
        latest_games.groupby('home_team')['home_elo'].last()
        .combine_first(latest_games.groupby('away_team')['away_elo'].last())
        .sort_values(ascending=False)
    )
    print(f"\nElo ratings — season {latest_season} (pre-game, top 10):")
    print(all_elos.head(10).to_string())
