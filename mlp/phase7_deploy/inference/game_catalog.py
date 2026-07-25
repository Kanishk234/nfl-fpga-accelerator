"""Read-only view over games_catalog.json — pyserial/pandas-free, shared by both UIs.

The catalog is produced by mlp/phase7_deploy/export_catalog.py (run in WSL). It holds each
game's precomputed 21 feature bytes plus metadata, so any UI can drive the board with
only pyserial. See PHASE7_COMPLETE.md "Live UI" for why the work is split this way.
"""
import json
from pathlib import Path

DEFAULT_CATALOG = Path(__file__).parent.parent / 'games_catalog.json'


class GameCatalog:

    def __init__(self, path=DEFAULT_CATALOG):
        data = json.loads(Path(path).read_text())
        # Stable per-game id = index in the (season, week, home)-sorted list.
        self.games = data['games']
        for i, g in enumerate(self.games):
            g.setdefault('gid', i)
        self.generated = data.get('generated', '?')

    def seasons(self):
        return sorted({g['season'] for g in self.games}, reverse=True)

    def teams(self, season=None):
        pool = self.games if season is None else [g for g in self.games
                                                  if g['season'] == season]
        return sorted({g['home'] for g in pool} | {g['away'] for g in pool})

    def filter(self, season, home=None, away=None):
        out = [g for g in self.games if g['season'] == season]
        if home:
            out = [g for g in out if g['home'] == home]
        if away:
            out = [g for g in out if g['away'] == away]
        return sorted(out, key=lambda g: (g['week'], g['home']))

    def by_gid(self, gid):
        return self.games[gid]

    @staticmethod
    def label(g):
        return f"W{g['week']:>2}  {g['home']} (H) vs {g['away']} (A)"
