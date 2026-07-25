import json
import pickle
import numpy as np
import pandas as pd
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

ARTIFACTS_DIR = Path('artifacts')


class FeatureBuilder:

    def __init__(self):
        meta          = json.load(open(ARTIFACTS_DIR / 'features.json'))
        self.features = meta['features']   # locked order — never change
        self.scaler   = pickle.load(open(ARTIFACTS_DIR / 'scaler.pkl', 'rb'))
        self.games    = pd.read_parquet('data/processed/games.parquet')
        logger.info(f"FeatureBuilder loaded: {len(self.features)} features")

    def from_historical_game(self, season: int, week: int,
                              home_team: str) -> tuple[list[int], dict]:
        """
        Look up a historical game from the processed dataset and build
        the feature vector.

        Returns: (feature_bytes, game_info_dict)
        """
        mask = (
            (self.games['season'] == season) &
            (self.games['week']   == week) &
            (self.games['home_team'].str.upper() == home_team.upper())
        )
        matches = self.games[mask]
        if len(matches) == 0:
            raise ValueError(
                f"Game not found: {home_team} (home) Week {week} {season}. "
                f"Available home teams this week: "
                f"{self.games[(self.games.season==season) & (self.games.week==week)]['home_team'].tolist()}"
            )

        row           = matches.iloc[0]
        feature_bytes = self._encode_row(row)

        game_info = {
            'home_team':     row['home_team'],
            'away_team':     row['away_team'],
            'season':        int(row['season']),
            'week':          int(row['week']),
            'actual_winner': row['home_team'] if row['home_win'] == 1
                             else row['away_team'],
            'actual_spread': int(row['home_score'] - row['away_score'])
                             if 'home_score' in row else None,
        }
        return feature_bytes, game_info

    def from_current_week(self, home_team: str,
                           away_team: str) -> tuple[list[int], dict]:
        """
        Build features for an upcoming game using the most recent
        available stats for both teams.
        """
        home_upper = home_team.upper()
        away_upper = away_team.upper()

        home_games = self.games[
            (self.games['home_team'].str.upper() == home_upper) |
            (self.games['away_team'].str.upper() == home_upper)
        ].sort_values(['season', 'week']).tail(1)

        away_games = self.games[
            (self.games['home_team'].str.upper() == away_upper) |
            (self.games['away_team'].str.upper() == away_upper)
        ].sort_values(['season', 'week']).tail(1)

        if len(home_games) == 0:
            raise ValueError(f"No games found for team: {home_team}")
        if len(away_games) == 0:
            raise ValueError(f"No games found for team: {away_team}")

        home_row = home_games.iloc[0]
        away_row = away_games.iloc[0]

        feature_dict  = self._build_matchup_features(home_row, away_row,
                                                      home_upper, away_upper)
        feature_bytes = self._encode_feature_dict(feature_dict)

        game_info = {
            'home_team':     home_team,
            'away_team':     away_team,
            'season':        'Current',
            'week':          'Upcoming',
            'actual_winner': None,
            'actual_spread': None,
        }
        return feature_bytes, game_info

    def _encode_row(self, row: pd.Series) -> list[int]:
        """Scale a game row and encode as 21 uint8 bytes."""
        X = self.scaler.transform(
            row[self.features].values.reshape(1, -1).astype('float32')
        )
        return np.clip(np.round(X[0] * 255), 0, 255).astype(int).tolist()

    def _build_matchup_features(self, home_row, away_row,
                                 home_team, away_team) -> dict:
        """
        Construct a feature dict for a matchup between two teams
        using their most recent game stats.
        """
        home_was_home = (home_row['home_team'].upper() == home_team)
        away_was_home = (away_row['home_team'].upper() == away_team)

        prefix_h = 'home_' if home_was_home else 'away_'
        prefix_a = 'home_' if away_was_home else 'away_'

        feat       = {}
        team_feats = [
            'elo', 'rest_days', 'win_streak',
            'pts_scored_avg', 'pts_allowed_avg', 'point_diff_avg',
        ]

        for f in team_feats:
            home_col = f'home_{f}'
            away_col = f'away_{f}'
            if home_col in self.features:
                feat[home_col] = home_row.get(f'{prefix_h}{f}',
                                               home_row.get(home_col, 0))
                feat[away_col] = away_row.get(f'{prefix_a}{f}',
                                               away_row.get(away_col, 0))

        # Game context features — use home team's last game as proxy.
        # vegas_spread/total/temp/wind are unknown for future games;
        # last game values are the best available approximation.
        for ctx in ['week', 'season_progress',
                    'vegas_spread', 'vegas_total', 'temp', 'wind']:
            if ctx in self.features:
                feat[ctx] = home_row.get(ctx, 0)

        # elo_diff must be recalculated for this specific matchup —
        # home_row['elo_diff'] was vs. a different opponent.
        if 'elo_diff' in self.features:
            feat['elo_diff'] = feat.get('home_elo', 0) - feat.get('away_elo', 0)

        # is_dome: look up from home team's actual home games so we get
        # their stadium, not the opponent's stadium from an away game.
        if 'is_dome' in self.features:
            home_home_games = self.games[
                self.games['home_team'].str.upper() == home_team
            ]
            feat['is_dome'] = (int(home_home_games['is_dome'].iloc[-1])
                               if len(home_home_games) > 0 else 0)

        # is_div_game depends on this specific matchup, not last game's opponent.
        if 'is_div_game' in self.features:
            prior_matchups = self.games[
                ((self.games['home_team'].str.upper() == home_team) &
                 (self.games['away_team'].str.upper() == away_team)) |
                ((self.games['home_team'].str.upper() == away_team) &
                 (self.games['away_team'].str.upper() == home_team))
            ]
            feat['is_div_game'] = (int(prior_matchups['is_div_game'].mode()[0])
                                   if len(prior_matchups) > 0 else 0)

        return feat

    def _encode_feature_dict(self, feat_dict: dict) -> list[int]:
        """Encode a feature dict into 21 uint8 bytes in canonical order."""
        values = np.array([feat_dict.get(f, 0.0) for f in self.features],
                          dtype='float32')
        X = self.scaler.transform(values.reshape(1, -1))
        return np.clip(np.round(X[0] * 255), 0, 255).astype(int).tolist()

    def get_available_teams(self) -> list[str]:
        """Return sorted list of all team abbreviations in the dataset."""
        teams = set(self.games['home_team'].tolist() +
                    self.games['away_team'].tolist())
        return sorted(teams)

    def get_available_seasons(self) -> list[int]:
        return sorted(self.games['season'].unique().tolist())

    def get_games_for_week(self, season: int, week: int) -> list[dict]:
        """Return all games for a given season/week."""
        mask  = (self.games['season'] == season) & (self.games['week'] == week)
        games = self.games[mask]
        return [
            {
                'home_team': row['home_team'],
                'away_team': row['away_team'],
                'home_win':  int(row['home_win']) if not pd.isna(row['home_win']) else None,
            }
            for _, row in games.iterrows()
        ]
