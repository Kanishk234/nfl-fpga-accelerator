"""
NFL FPGA Predictor — desktop UI.

Runs on Windows (native COM port) with ONLY pyserial as a dependency: game features
are precomputed into phase7_deploy/games_catalog.json by export_catalog.py (run once in
WSL, where the pandas/scikit-learn data stack lives). Pick a season, optionally filter by
home/away team, choose a game, and run it on the FPGA — the board computes the MLP and the
UI shows win probability + point spread.

    # one-time / whenever the dataset changes (WSL):
    source venv/bin/activate && python phase7_deploy/export_catalog.py
    # then, on Windows with the board on a COM port:
    python phase7_deploy/ui/app.py
"""
import json
import sys
import time
import threading
from pathlib import Path

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import serial.tools.list_ports

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from phase7_deploy.inference.fpga_client import FPGAClient

CATALOG_PATH = Path(__file__).parent.parent / 'games_catalog.json'

# Pipeline-stage panel: real stages of one inference. The transfer is ~12 ms end-to-end,
# so the dwell below is purely so a human can SEE the pipeline advance; the true latency
# is reported separately from the board's measured round-trip.
STAGE_DWELL_S = 0.12
STAGES = ["Encode", "TX 23B", "FPGA MLP", "RX 4B"]


class GameCatalog:
    """Read-only view over games_catalog.json — no pandas needed."""

    def __init__(self, path: Path):
        data = json.loads(path.read_text())
        self.games     = data['games']
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

    @staticmethod
    def label(g):
        return f"W{g['week']:>2}  {g['home']} (H) vs {g['away']} (A)"


class NFLFPGAApp:

    def __init__(self, root):
        self.root    = root
        self.client  = None
        self.catalog = None
        self.shown   = []      # games currently in the Game dropdown

        root.title("NFL FPGA Predictor")
        root.geometry("760x680")
        root.minsize(720, 640)

        try:
            self.catalog = GameCatalog(CATALOG_PATH)
        except FileNotFoundError:
            self._build_missing_catalog_ui()
            return

        self._build_ui()
        self._refresh_ports()
        self._season_changed()   # populate Home/Away team filters + the game list

    # ── layout ────────────────────────────────────────────────────────

    def _build_missing_catalog_ui(self):
        msg = ("games_catalog.json not found.\n\n"
               "Generate it once in WSL (where pandas/scikit-learn live):\n\n"
               "    source venv/bin/activate\n"
               "    python phase7_deploy/export_catalog.py\n\n"
               "then relaunch this app on Windows.")
        tk.Label(self.root, text=msg, justify=tk.LEFT, font=('Courier', 11),
                 padx=20, pady=20).pack(fill=tk.BOTH, expand=True)

    def _build_ui(self):
        # top bar
        top = tk.Frame(self.root, bg='#1a1a2e', pady=8)
        top.pack(fill=tk.X)
        tk.Label(top, text="🏈 NFL FPGA Predictor", font=('Helvetica', 16, 'bold'),
                 bg='#1a1a2e', fg='white').pack(side=tk.LEFT, padx=15)
        self.status_label = tk.Label(top, text="● Disconnected", font=('Helvetica', 11),
                                     bg='#1a1a2e', fg='#ff6b6b')
        self.status_label.pack(side=tk.RIGHT, padx=15)

        content = tk.Frame(self.root)
        content.pack(fill=tk.BOTH, expand=True, padx=10, pady=6)

        left  = tk.LabelFrame(content, text="Connection", padx=8, pady=8)
        right = tk.LabelFrame(content, text="Game Selection", padx=8, pady=8)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 6))
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # connection panel
        tk.Label(left, text="Port:").grid(row=0, column=0, sticky=tk.W)
        self.port_var   = tk.StringVar()
        self.port_combo = ttk.Combobox(left, textvariable=self.port_var, width=12,
                                       state='readonly')
        self.port_combo.grid(row=0, column=1, padx=5, pady=2)
        tk.Button(left, text="Refresh", command=self._refresh_ports).grid(
            row=1, columnspan=2, pady=2, sticky=tk.EW)
        self.connect_btn = tk.Button(left, text="Connect", command=self._connect,
                                     bg='#4caf50', fg='white', width=12)
        self.connect_btn.grid(row=2, columnspan=2, pady=2, sticky=tk.EW)
        tk.Button(left, text="Disconnect", command=self._disconnect, width=12).grid(
            row=3, columnspan=2, pady=2, sticky=tk.EW)
        tk.Label(left, text=f"catalog:\n{self.catalog.generated}", justify=tk.LEFT,
                 font=('Helvetica', 7), fg='gray').grid(row=5, columnspan=2, pady=(10, 0))

        # selection panel — Season, Home filter, Away filter, Game
        r = 0
        tk.Label(right, text="Season:").grid(row=r, column=0, sticky=tk.W, pady=3)
        self.season_var = tk.StringVar(value=str(self.catalog.seasons()[0]))
        ttk.Combobox(right, textvariable=self.season_var,
                     values=[str(s) for s in self.catalog.seasons()], width=10,
                     state='readonly').grid(row=r, column=1, sticky=tk.W, padx=5)
        self.season_var.trace_add('write', lambda *a: self._season_changed())

        r += 1
        tk.Label(right, text="Home (opt):").grid(row=r, column=0, sticky=tk.W, pady=3)
        self.home_var = tk.StringVar(value='Any')
        self.home_combo = ttk.Combobox(right, textvariable=self.home_var, width=10,
                                       state='readonly')
        self.home_combo.grid(row=r, column=1, sticky=tk.W, padx=5)
        self.home_var.trace_add('write', lambda *a: self._reload_games())

        tk.Label(right, text="Away (opt):").grid(row=r, column=2, sticky=tk.W, padx=(10, 0))
        self.away_var = tk.StringVar(value='Any')
        self.away_combo = ttk.Combobox(right, textvariable=self.away_var, width=10,
                                       state='readonly')
        self.away_combo.grid(row=r, column=3, sticky=tk.W, padx=5)
        self.away_var.trace_add('write', lambda *a: self._reload_games())

        r += 1
        tk.Label(right, text="Game:").grid(row=r, column=0, sticky=tk.W, pady=3)
        self.game_var   = tk.StringVar()
        self.game_combo = ttk.Combobox(right, textvariable=self.game_var, width=34,
                                       state='readonly')
        self.game_combo.grid(row=r, column=1, columnspan=3, sticky=tk.W, padx=5)

        r += 1
        self.count_label = tk.Label(right, text="", font=('Helvetica', 8), fg='gray')
        self.count_label.grid(row=r, column=1, columnspan=3, sticky=tk.W)

        r += 1
        self.run_btn = tk.Button(right, text="Run Inference", command=self._run_inference,
                                 font=('Helvetica', 12, 'bold'), bg='#2196f3', fg='white',
                                 width=22, pady=6, state=tk.DISABLED)
        self.run_btn.grid(row=r, column=0, columnspan=4, pady=12)

        # pipeline-stage indicator
        stage_frame = tk.LabelFrame(self.root, text="FPGA Pipeline", padx=10, pady=6)
        stage_frame.pack(fill=tk.X, padx=10, pady=(0, 4))
        self.stage_dots = {}
        row = tk.Frame(stage_frame); row.pack()
        for i, name in enumerate(STAGES):
            cell = tk.Frame(row); cell.pack(side=tk.LEFT, padx=6)
            dot = tk.Label(cell, text="●", font=('Helvetica', 18), fg='#cccccc')
            dot.pack()
            tk.Label(cell, text=name, font=('Helvetica', 8)).pack()
            self.stage_dots[i] = dot
            if i < len(STAGES) - 1:
                tk.Label(row, text="→", font=('Helvetica', 14), fg='#999').pack(side=tk.LEFT)

        # prediction display
        pred = tk.LabelFrame(self.root, text="Prediction", padx=10, pady=8)
        pred.pack(fill=tk.X, padx=10, pady=4)
        self.pred_title = tk.Label(pred, text="—", font=('Helvetica', 13, 'bold'))
        self.pred_title.pack()
        bar = tk.Frame(pred); bar.pack(fill=tk.X, pady=4)
        tk.Label(bar, text="Win probability:").pack(side=tk.LEFT)
        self.prob_bar = ttk.Progressbar(bar, length=260, mode='determinate')
        self.prob_bar.pack(side=tk.LEFT, padx=8)
        self.prob_label = tk.Label(bar, text="—", font=('Helvetica', 12, 'bold'),
                                   fg='#2196f3')
        self.prob_label.pack(side=tk.LEFT)
        self.spread_label = tk.Label(pred, text="Spread: —", font=('Helvetica', 11))
        self.spread_label.pack()
        self.meta_label = tk.Label(pred, text="", font=('Helvetica', 9), fg='gray')
        self.meta_label.pack()

        # history
        hist = tk.LabelFrame(self.root, text="History", padx=8, pady=5)
        hist.pack(fill=tk.BOTH, expand=True, padx=10, pady=4)
        self.history_text = scrolledtext.ScrolledText(hist, height=6, font=('Courier', 9),
                                                      state=tk.DISABLED)
        self.history_text.pack(fill=tk.BOTH, expand=True)

    # ── selection logic ───────────────────────────────────────────────

    def _season_changed(self):
        # repopulate team filters for the new season, reset to Any, reload games
        teams = self.catalog.teams(int(self.season_var.get()))
        self.home_combo['values'] = ['Any'] + teams
        self.away_combo['values'] = ['Any'] + teams
        self.home_var.set('Any')
        self.away_var.set('Any')   # traces fire _reload_games

    def _reload_games(self, *args):
        season = int(self.season_var.get())
        home   = None if self.home_var.get() in ('', 'Any') else self.home_var.get()
        away   = None if self.away_var.get() in ('', 'Any') else self.away_var.get()
        self.shown = self.catalog.filter(season, home, away)
        labels     = [GameCatalog.label(g) for g in self.shown]
        self.game_combo['values'] = labels
        if labels:
            self.game_var.set(labels[0])
            self.count_label.config(text=f"{len(labels)} game(s) match", fg='gray')
        else:
            self.game_var.set('')
            self.count_label.config(text="no game matches that matchup this season",
                                    fg='#ff6b6b')
        self._update_run_state()

    def _selected_game(self):
        i = self.game_combo.current()
        return self.shown[i] if 0 <= i < len(self.shown) else None

    def _update_run_state(self):
        ok = (self.client is not None and self.client.is_connected()
              and self._selected_game() is not None)
        self.run_btn.config(state=tk.NORMAL if ok else tk.DISABLED)

    # ── connection ────────────────────────────────────────────────────

    def _refresh_ports(self):
        # Show "COM8 — USB Serial Port" so the board's FT2232 UART is identifiable;
        # keep a label→device map so _connect can recover the bare port name.
        self._port_map = {}
        labels = []
        for p in serial.tools.list_ports.comports():
            label = f"{p.device} — {p.description}" if p.description else p.device
            self._port_map[label] = p.device
            labels.append(label)
        self.port_combo['values'] = labels
        if labels and not self.port_var.get():
            self.port_var.set(labels[0])

    def _connect(self):
        label = self.port_var.get()
        port  = self._port_map.get(label, label)   # tolerate a bare "COM8" too
        if not port:
            messagebox.showerror("Error", "Select a COM port first")
            return
        try:
            self.client = FPGAClient(port)
            self.client.connect()
            self.status_label.config(text=f"● Connected ({port})", fg='#4caf50')
            self.connect_btn.config(state=tk.DISABLED)
        except Exception as e:
            messagebox.showerror("Connection failed", str(e))
            self.client = None
        self._update_run_state()

    def _disconnect(self):
        if self.client:
            self.client.disconnect()
            self.client = None
        self.status_label.config(text="● Disconnected", fg='#ff6b6b')
        self.connect_btn.config(state=tk.NORMAL)
        self._update_run_state()

    # ── inference ─────────────────────────────────────────────────────

    def _set_stage(self, idx):
        for i, dot in self.stage_dots.items():
            dot.config(fg='#4caf50' if i <= idx else '#cccccc')

    def _reset_stages(self):
        for dot in self.stage_dots.values():
            dot.config(fg='#cccccc')

    def _run_inference(self):
        game = self._selected_game()
        if game is None:
            return
        self.run_btn.config(state=tk.DISABLED, text="Running...")
        self._reset_stages()
        threading.Thread(target=self._worker, args=(game,), daemon=True).start()

    def _worker(self, game):
        try:
            self.root.after(0, self._set_stage, 0)          # Encode (already precomputed)
            time.sleep(STAGE_DWELL_S)
            self.root.after(0, self._set_stage, 1)          # TX
            time.sleep(STAGE_DWELL_S)
            result = self.client.run_inference(game['bytes'])   # actual round-trip
            self.root.after(0, self._set_stage, 2)          # MLP done
            time.sleep(STAGE_DWELL_S)
            self.root.after(0, self._set_stage, 3)          # RX
            self.root.after(0, self._display, game, result)
        except Exception as e:
            msg = str(e)   # bind now: `e` is cleared when the except block exits
            self.root.after(0, self._reset_stages)
            self.root.after(0, lambda m=msg: messagebox.showerror("Inference Error", m))
        finally:
            self.root.after(0, lambda: self.run_btn.config(state=tk.NORMAL,
                                                           text="Run Inference"))

    def _display(self, game, result):
        home, away = game['home'], game['away']
        win  = result['win_prob']
        sprd = result['spread']

        self.pred_title.config(text=f"{home} (home)  vs  {away} (away)  —  "
                                    f"Week {game['week']}, {game['season']}")
        self.prob_bar['value'] = win * 100
        winner  = home if win >= 0.5 else away
        wdisp   = win if win >= 0.5 else 1 - win
        self.prob_label.config(text=f"{winner}  {wdisp:.1%}",
                               fg='#4caf50' if result['status'] == 'OK' else '#ff9800')

        # Betting notation: the favorite lays points (shown negative).
        if sprd > 0:      spread_str = f"{home} favored by {sprd} ({home} -{sprd})"
        elif sprd < 0:    spread_str = f"{away} favored by {abs(sprd)} ({away} -{abs(sprd)})"
        else:             spread_str = "Pick 'em (0)"
        self.spread_label.config(text=f"Spread: {spread_str}")

        actual = ""
        if game.get('actual_winner'):
            correct = game['actual_winner'].upper() == winner.upper()
            actual  = f"  |  Actual: {game['actual_winner']} won {'✓' if correct else '✗'}"
        self.meta_label.config(text=f"Latency {result['latency_ms']:.1f} ms   "
                                    f"Status {result['status']}   "
                                    f"raw win={result['raw_win']}/256{actual}")

        entry = (f"{game['season']} W{game['week']:>2}  {home:<4} vs {away:<4}  "
                 f"{winner} {wdisp:.0%}  spread {sprd:+d}")
        if game.get('actual_winner'):
            entry += f"  [{'OK' if game['actual_winner'].upper()==winner.upper() else 'X'}]"
        self.history_text.config(state=tk.NORMAL)
        self.history_text.insert('1.0', entry + '\n')
        self.history_text.config(state=tk.DISABLED)


def main():
    root = tk.Tk()
    app  = NFLFPGAApp(root)
    root.protocol("WM_DELETE_WINDOW", lambda: (app._disconnect()
                  if app.client else None, root.destroy()))
    root.mainloop()


if __name__ == '__main__':
    main()
