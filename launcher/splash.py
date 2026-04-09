"""
splash.py — Tkinter splash screen for Nikola launcher.

Shows a 420×240 dark window with a progress bar and status label
while the launcher performs background initialisation. Call
`update(message, percent)` from the main thread to refresh the UI.
"""

import tkinter as tk
from tkinter import ttk
import threading


class SplashScreen:
    """Lightweight splash window; must be driven from the main thread."""

    BG = "#0a0a14"
    FG = "#e0e0ff"
    ACCENT = "#5555ff"
    WIDTH = 420
    HEIGHT = 240

    def __init__(self) -> None:
        self._root = tk.Tk()
        self._root.overrideredirect(True)          # no title bar
        self._root.configure(bg=self.BG)
        self._root.resizable(False, False)
        self._root.attributes("-topmost", True)

        # Centre on screen
        sw = self._root.winfo_screenwidth()
        sh = self._root.winfo_screenheight()
        x = (sw - self.WIDTH) // 2
        y = (sh - self.HEIGHT) // 2
        self._root.geometry(f"{self.WIDTH}x{self.HEIGHT}+{x}+{y}")

        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = self._root

        # Logo / title
        tk.Label(
            root,
            text="NIKOLA",
            font=("Segoe UI", 28, "bold"),
            bg=self.BG,
            fg=self.ACCENT,
        ).pack(pady=(30, 0))

        tk.Label(
            root,
            text="AI Personal Assistant",
            font=("Segoe UI", 10),
            bg=self.BG,
            fg=self.FG,
        ).pack()

        # Status message
        self._status_var = tk.StringVar(value="Starting…")
        tk.Label(
            root,
            textvariable=self._status_var,
            font=("Segoe UI", 9),
            bg=self.BG,
            fg="#aaaacc",
        ).pack(pady=(20, 4))

        # Progress bar
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure(
            "Nikola.Horizontal.TProgressbar",
            troughcolor=self.BG,
            background=self.ACCENT,
            bordercolor=self.BG,
            lightcolor=self.ACCENT,
            darkcolor=self.ACCENT,
        )
        self._progress_var = tk.IntVar(value=0)
        self._bar = ttk.Progressbar(
            root,
            style="Nikola.Horizontal.TProgressbar",
            variable=self._progress_var,
            maximum=100,
            length=360,
            mode="determinate",
        )
        self._bar.pack(pady=4)

        # Percentage label
        self._pct_var = tk.StringVar(value="0%")
        tk.Label(
            root,
            textvariable=self._pct_var,
            font=("Segoe UI", 8),
            bg=self.BG,
            fg="#aaaacc",
        ).pack()

    # ------------------------------------------------------------------
    # Public API (may be called from any thread via `after`)
    # ------------------------------------------------------------------

    def update(self, message: str, percent: int) -> None:
        """Thread-safe update of status text and progress bar."""
        self._root.after(0, self._apply_update, message, percent)

    def _apply_update(self, message: str, percent: int) -> None:
        self._status_var.set(message)
        self._progress_var.set(percent)
        self._pct_var.set(f"{percent}%")
        self._root.update_idletasks()

    def pump(self) -> None:
        """Process pending Tk events (call from main thread in a loop)."""
        self._root.update()

    def close(self) -> None:
        """Destroy the splash window."""
        self._root.after(0, self._root.destroy)

    def run_until_done(self, done_event: threading.Event) -> None:
        """
        Block (pumping Tk events) until *done_event* is set, then close.
        This should be called from the main thread.
        """
        while not done_event.is_set():
            try:
                self._root.update()
            except tk.TclError:
                break
        self.close()
