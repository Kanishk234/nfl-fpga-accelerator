import csv
import os
from datetime import datetime
from pathlib import Path

LOG_PATH = Path('phase7_deploy/logs/inference_log.csv')
COLUMNS  = [
    'timestamp', 'home_team', 'away_team', 'season', 'week',
    'fpga_win_prob', 'fpga_spread', 'status',
    'latency_ms', 'actual_winner', 'actual_spread',
]


def log_prediction(game_info: dict, result: dict):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    write_header = not LOG_PATH.exists()
    with open(LOG_PATH, 'a', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        if write_header:
            writer.writeheader()
        writer.writerow({
            'timestamp':     datetime.now().isoformat(),
            'home_team':     game_info.get('home_team', ''),
            'away_team':     game_info.get('away_team', ''),
            'season':        game_info.get('season', ''),
            'week':          game_info.get('week', ''),
            'fpga_win_prob': f"{result['win_prob']:.4f}",
            'fpga_spread':   result['spread'],
            'status':        result['status'],
            'latency_ms':    f"{result['latency_ms']:.1f}",
            'actual_winner': game_info.get('actual_winner', ''),
            'actual_spread': game_info.get('actual_spread', ''),
        })
