import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import threading
import serial.tools.list_ports
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from phase7_deploy.inference.fpga_client import FPGAClient
from phase7_deploy.inference.feature_builder import FeatureBuilder
from phase7_deploy.inference.logger import log_prediction


class NFLFPGAApp:

    def __init__(self, root):
        self.root    = root
        self.client  = None
        self.builder = FeatureBuilder()
        self.history = []

        root.title("NFL FPGA Predictor")
        root.geometry("750x650")
        root.resizable(True, True)

        self._build_ui()
        self._refresh_ports()

    def _build_ui(self):
        # ── Top bar ──────────────────────────────────────────────────
        top = tk.Frame(self.root, bg='#1a1a2e', pady=8)
        top.pack(fill=tk.X)
        tk.Label(top, text="NFL FPGA Predictor",
                 font=('Helvetica', 16, 'bold'),
                 bg='#1a1a2e', fg='white').pack(side=tk.LEFT, padx=15)
        self.status_label = tk.Label(top, text="● Disconnected",
                                      font=('Helvetica', 11),
                                      bg='#1a1a2e', fg='#ff6b6b')
        self.status_label.pack(side=tk.RIGHT, padx=15)

        # ── Main content ─────────────────────────────────────────────
        content = tk.Frame(self.root)
        content.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        left  = tk.LabelFrame(content, text="Connection", padx=8, pady=8)
        right = tk.LabelFrame(content, text="Game Selection", padx=8, pady=8)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 5))
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # ── Connection panel ─────────────────────────────────────────
        tk.Label(left, text="Port:").grid(row=0, column=0, sticky=tk.W)
        self.port_var   = tk.StringVar()
        self.port_combo = ttk.Combobox(left, textvariable=self.port_var,
                                        width=12, state='readonly')
        self.port_combo.grid(row=0, column=1, padx=5, pady=2)

        tk.Button(left, text="Refresh",
                  command=self._refresh_ports).grid(row=1, columnspan=2, pady=2)
        self.connect_btn = tk.Button(left, text="Connect",
                                      command=self._connect,
                                      bg='#4caf50', fg='white', width=12)
        self.connect_btn.grid(row=2, columnspan=2, pady=2)
        tk.Button(left, text="Disconnect",
                  command=self._disconnect, width=12).grid(
                      row=3, columnspan=2, pady=2)
        tk.Button(left, text="Reset Board",
                  command=self._reset_board, width=12).grid(
                      row=4, columnspan=2, pady=2)

        # ── Game selection panel ──────────────────────────────────────
        self.mode_var = tk.StringVar(value='historical')
        tk.Radiobutton(right, text="Historical game",
                       variable=self.mode_var, value='historical',
                       command=self._update_mode).grid(
                           row=0, column=0, sticky=tk.W, pady=2)
        tk.Radiobutton(right, text="Custom matchup",
                       variable=self.mode_var, value='matchup',
                       command=self._update_mode).grid(
                           row=0, column=1, sticky=tk.W, pady=2)

        # Historical mode widgets
        self.hist_frame = tk.Frame(right)
        self.hist_frame.grid(row=1, column=0, columnspan=3,
                              sticky=tk.W, pady=4)

        tk.Label(self.hist_frame, text="Season:").grid(
            row=0, column=0, sticky=tk.W)
        self.season_var = tk.StringVar(value='2024')
        seasons         = [str(s) for s in self.builder.get_available_seasons()]
        ttk.Combobox(self.hist_frame, textvariable=self.season_var,
                     values=seasons, width=8,
                     state='readonly').grid(row=0, column=1, padx=5)

        tk.Label(self.hist_frame, text="Week:").grid(
            row=0, column=2, sticky=tk.W, padx=(10, 0))
        self.week_var   = tk.StringVar(value='1')
        self.week_combo = ttk.Combobox(
            self.hist_frame, textvariable=self.week_var,
            values=[str(i) for i in range(1, 19)],
            width=5, state='readonly')
        self.week_combo.grid(row=0, column=3, padx=5)
        self.week_combo.bind('<<ComboboxSelected>>', self._update_games)
        self.season_var.trace_add('write', lambda *a: self._update_games())

        tk.Label(self.hist_frame, text="Game:").grid(
            row=1, column=0, sticky=tk.W, pady=4)
        self.game_var   = tk.StringVar()
        self.game_combo = ttk.Combobox(
            self.hist_frame, textvariable=self.game_var,
            width=25, state='readonly')
        self.game_combo.grid(row=1, column=1, columnspan=3,
                              padx=5, sticky=tk.W)

        # Custom matchup widgets
        self.matchup_frame = tk.Frame(right)
        teams = self.builder.get_available_teams()
        tk.Label(self.matchup_frame, text="Home:").grid(
            row=0, column=0, sticky=tk.W)
        self.home_var = tk.StringVar()
        ttk.Combobox(self.matchup_frame, textvariable=self.home_var,
                     values=teams, width=8,
                     state='readonly').grid(row=0, column=1, padx=5)
        tk.Label(self.matchup_frame, text="Away:").grid(
            row=0, column=2, sticky=tk.W, padx=(10, 0))
        self.away_var = tk.StringVar()
        ttk.Combobox(self.matchup_frame, textvariable=self.away_var,
                     values=teams, width=8,
                     state='readonly').grid(row=0, column=3, padx=5)

        self._update_mode()
        self._update_games()

        # Run button
        self.run_btn = tk.Button(right, text="Run Inference",
                                  command=self._run_inference,
                                  font=('Helvetica', 12, 'bold'),
                                  bg='#2196f3', fg='white',
                                  width=20, pady=6,
                                  state=tk.DISABLED)
        self.run_btn.grid(row=3, column=0, columnspan=3, pady=10)

        # ── Prediction display ────────────────────────────────────────
        pred_frame = tk.LabelFrame(self.root, text="Prediction",
                                    padx=10, pady=8)
        pred_frame.pack(fill=tk.X, padx=10, pady=5)

        self.pred_title = tk.Label(pred_frame, text="—",
                                    font=('Helvetica', 13, 'bold'))
        self.pred_title.pack()

        bar_frame = tk.Frame(pred_frame)
        bar_frame.pack(fill=tk.X, pady=4)
        tk.Label(bar_frame, text="Win probability:").pack(side=tk.LEFT)
        self.prob_bar = ttk.Progressbar(bar_frame, length=250,
                                         mode='determinate')
        self.prob_bar.pack(side=tk.LEFT, padx=8)
        self.prob_label = tk.Label(bar_frame, text="—",
                                    font=('Helvetica', 12, 'bold'),
                                    fg='#2196f3')
        self.prob_label.pack(side=tk.LEFT)

        self.spread_label = tk.Label(pred_frame, text="Spread: —",
                                      font=('Helvetica', 11))
        self.spread_label.pack()

        self.meta_label = tk.Label(pred_frame, text="",
                                    font=('Helvetica', 9), fg='gray')
        self.meta_label.pack()

        # ── History ───────────────────────────────────────────────────
        hist_frame = tk.LabelFrame(self.root, text="Prediction History",
                                    padx=8, pady=5)
        hist_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        self.history_text = scrolledtext.ScrolledText(
            hist_frame, height=6, font=('Courier', 9), state=tk.DISABLED)
        self.history_text.pack(fill=tk.BOTH, expand=True)

    # ── Connection methods ────────────────────────────────────────────

    def _refresh_ports(self):
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self.port_combo['values'] = ports
        if ports and not self.port_var.get():
            self.port_var.set(ports[0])

    def _connect(self):
        port = self.port_var.get()
        if not port:
            messagebox.showerror("Error", "Select a COM port first")
            return
        try:
            self.client = FPGAClient(port)
            self.client.connect()
            self.status_label.config(text=f"● Connected ({port})", fg='#4caf50')
            self.connect_btn.config(state=tk.DISABLED)
            self.run_btn.config(state=tk.NORMAL)
        except Exception as e:
            messagebox.showerror("Connection failed", str(e))
            self.client = None

    def _disconnect(self):
        if self.client:
            self.client.disconnect()
            self.client = None
        self.status_label.config(text="● Disconnected", fg='#ff6b6b')
        self.connect_btn.config(state=tk.NORMAL)
        self.run_btn.config(state=tk.DISABLED)

    def _reset_board(self):
        if self.client and self.client.is_connected():
            self.client.flush_host_buffers()
            self.meta_label.config(
                text="Host buffers flushed. To reset the FPGA, press BTNC on the board.")

    # ── Game selection methods ────────────────────────────────────────

    def _update_mode(self):
        if self.mode_var.get() == 'historical':
            self.hist_frame.grid()
            self.matchup_frame.grid_remove()
        else:
            self.hist_frame.grid_remove()
            self.matchup_frame.grid(row=1, column=0,
                                     columnspan=3, sticky=tk.W, pady=4)

    def _update_games(self, *args):
        try:
            season  = int(self.season_var.get())
            week    = int(self.week_var.get())
            games   = self.builder.get_games_for_week(season, week)
            options = [f"{g['home_team']} vs {g['away_team']}" for g in games]
            self.game_combo['values'] = options
            if options:
                self.game_var.set(options[0])
        except Exception:
            pass

    # ── Inference ────────────────────────────────────────────────────

    def _run_inference(self):
        """Run inference in a background thread to keep UI responsive."""
        self.run_btn.config(state=tk.DISABLED, text="Running...")
        thread = threading.Thread(target=self._inference_worker, daemon=True)
        thread.start()

    def _inference_worker(self):
        try:
            game_info, feature_bytes = self._build_features()
            result = self.client.run_inference(feature_bytes)
            log_prediction(game_info, result)
            self.root.after(0, self._display_result, game_info, result)
        except Exception as e:
            self.root.after(0, self._show_error, str(e))
        finally:
            self.root.after(0, lambda: self.run_btn.config(
                state=tk.NORMAL, text="Run Inference"))

    def _build_features(self):
        if self.mode_var.get() == 'historical':
            game_str = self.game_var.get()
            if not game_str:
                raise ValueError("No game selected")
            home   = game_str.split(' vs ')[0].strip()
            season = int(self.season_var.get())
            week   = int(self.week_var.get())
            feature_bytes, game_info = self.builder.from_historical_game(
                season, week, home)
        else:
            home = self.home_var.get()
            away = self.away_var.get()
            if not home or not away:
                raise ValueError("Select both home and away teams")
            if home == away:
                raise ValueError("Home and away teams must be different")
            feature_bytes, game_info = self.builder.from_current_week(
                home, away)
        return game_info, feature_bytes

    def _display_result(self, game_info, result):
        home = game_info['home_team']
        away = game_info['away_team']
        win  = result['win_prob']

        self.pred_title.config(
            text=f"{home}  vs  {away}  —  "
                 f"Week {game_info['week']}, {game_info['season']}")

        self.prob_bar['value'] = win * 100
        predicted_winner = home if win >= 0.5 else away
        win_display      = win if win >= 0.5 else 1 - win
        self.prob_label.config(
            text=f"{predicted_winner}  {win_display:.1%}",
            fg='#4caf50' if result['status'] == 'OK' else '#ff9800')

        sprd = result['spread']
        if sprd < 0:
            spread_str = f"{home} {sprd:d}"
        elif sprd > 0:
            spread_str = f"{away} -{sprd:d}"
        else:
            spread_str = "Pick 'em"
        self.spread_label.config(text=f"Spread: {spread_str}")

        confidence = ("Low (<55%)"      if win_display < 0.55
                      else "Moderate (55-70%)" if win_display < 0.70
                      else "High (>70%)")
        actual_str = ""
        if game_info.get('actual_winner'):
            correct    = game_info['actual_winner'].upper() == predicted_winner.upper()
            actual_str = (f"  |  Actual winner: {game_info['actual_winner']} "
                          f"{'[OK]' if correct else '[X]'}")
        self.meta_label.config(
            text=f"Latency: {result['latency_ms']:.1f}ms  "
                 f"Status: {result['status']}  "
                 f"Confidence: {confidence}{actual_str}")

        entry = (f"{home:<4} vs {away:<4}  "
                 f"W:{game_info['week']}  "
                 f"{predicted_winner} {win_display:.0%}  "
                 f"Sprd:{sprd:+d}")
        if game_info.get('actual_winner'):
            correct = game_info['actual_winner'].upper() == predicted_winner.upper()
            entry  += f"  {'OK' if correct else 'X'}"
        self.history.append(entry)

        self.history_text.config(state=tk.NORMAL)
        self.history_text.insert('1.0', entry + '\n')
        self.history_text.config(state=tk.DISABLED)

    def _show_error(self, msg):
        messagebox.showerror("Inference Error", msg)


def main():
    root = tk.Tk()
    app  = NFLFPGAApp(root)
    root.protocol("WM_DELETE_WINDOW",
                  lambda: (app._disconnect(), root.destroy()))
    root.mainloop()


if __name__ == '__main__':
    main()
