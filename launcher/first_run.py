"""
first_run.py — 5-step first-run wizard for Nikola.

Collects Telegram credentials, User IDs, and storage paths,
then writes a .env file to the project root.
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from typing import Optional
import logging

log = logging.getLogger(__name__)


class FirstRunWizard:
    """
    A Tk-based wizard with 5 pages collected one at a time.
    Blocking: call `run()` from the main thread; it returns True on success.
    """

    BG = "#0a0a14"
    FG = "#e0e0ff"
    ENTRY_BG = "#1a1a2e"
    ACCENT = "#5555ff"
    FONT = ("Segoe UI", 10)
    TITLE_FONT = ("Segoe UI", 14, "bold")

    def __init__(self, env_path: Optional[Path] = None) -> None:
        self._env_path = env_path or (Path(__file__).parent.parent / ".env")
        self._values: dict[str, str] = {}
        self._current_step = 0
        self._result = False

        self._root = tk.Tk()
        self._root.title("Nikola — First Run Setup")
        self._root.configure(bg=self.BG)
        self._root.resizable(False, False)
        self._root.attributes("-topmost", True)
        w, h = 500, 360
        sw = self._root.winfo_screenwidth()
        sh = self._root.winfo_screenheight()
        self._root.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

        self._steps = [
            self._step_welcome,
            self._step_telegram_bot,
            self._step_telegram_users,
            self._step_vault_dir,
            self._step_confirm,
        ]
        self._frame: Optional[tk.Frame] = None
        self._show_step(0)

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _show_step(self, index: int) -> None:
        if self._frame:
            self._frame.destroy()
        self._frame = tk.Frame(self._root, bg=self.BG)
        self._frame.pack(fill=tk.BOTH, expand=True, padx=30, pady=20)
        self._steps[index]()
        self._current_step = index

    def _next(self) -> None:
        if self._current_step < len(self._steps) - 1:
            self._show_step(self._current_step + 1)

    def _back(self) -> None:
        if self._current_step > 0:
            self._show_step(self._current_step - 1)

    def _finish(self) -> None:
        try:
            self._write_env()
            self._result = True
            self._root.destroy()
        except Exception as exc:
            messagebox.showerror("Error", f"Failed to save .env:\n{exc}")

    # ------------------------------------------------------------------
    # Shared UI helpers
    # ------------------------------------------------------------------

    def _header(self, title: str, subtitle: str) -> None:
        tk.Label(
            self._frame,
            text=title,
            font=self.TITLE_FONT,
            bg=self.BG,
            fg=self.ACCENT,
        ).pack(anchor="w")
        tk.Label(
            self._frame,
            text=subtitle,
            font=self.FONT,
            bg=self.BG,
            fg="#aaaacc",
            wraplength=440,
            justify="left",
        ).pack(anchor="w", pady=(0, 16))

    def _labeled_entry(
        self, label: str, key: str, show: str = ""
    ) -> tk.Entry:
        tk.Label(
            self._frame, text=label, font=self.FONT, bg=self.BG, fg=self.FG
        ).pack(anchor="w")
        var = tk.StringVar(value=self._values.get(key, ""))
        entry = tk.Entry(
            self._frame,
            textvariable=var,
            font=self.FONT,
            bg=self.ENTRY_BG,
            fg=self.FG,
            insertbackground=self.FG,
            relief="flat",
            show=show,
        )
        entry.pack(fill="x", pady=(0, 10))
        entry.bind("<FocusOut>", lambda _e: self._values.update({key: var.get()}))
        entry.bind("<KeyRelease>", lambda _e: self._values.update({key: var.get()}))
        return entry

    def _nav_buttons(self, show_back: bool = True, finish: bool = False) -> None:
        bar = tk.Frame(self._frame, bg=self.BG)
        bar.pack(side="bottom", fill="x", pady=(10, 0))
        if show_back:
            tk.Button(
                bar,
                text="← Back",
                font=self.FONT,
                bg=self.ENTRY_BG,
                fg=self.FG,
                relief="flat",
                command=self._back,
            ).pack(side="left")
        if finish:
            tk.Button(
                bar,
                text="Finish →",
                font=self.FONT,
                bg=self.ACCENT,
                fg="white",
                relief="flat",
                command=self._finish,
            ).pack(side="right")
        else:
            tk.Button(
                bar,
                text="Next →",
                font=self.FONT,
                bg=self.ACCENT,
                fg="white",
                relief="flat",
                command=self._next,
            ).pack(side="right")

    # ------------------------------------------------------------------
    # Steps
    # ------------------------------------------------------------------

    def _step_welcome(self) -> None:
        self._header("Welcome to Nikola", "Step 1 of 5 — Let's get you set up.")
        tk.Label(
            self._frame,
            text=(
                "This wizard will configure your Telegram bot tokens,\n"
                "allowed user IDs, and local storage paths.\n\n"
                "Your settings will be saved to a local .env file."
            ),
            font=self.FONT,
            bg=self.BG,
            fg=self.FG,
            justify="left",
        ).pack(anchor="w", pady=20)
        self._nav_buttons(show_back=False)

    def _step_telegram_bot(self) -> None:
        self._header(
            "Telegram Bot Token",
            "Step 2 of 5 — Enter your Telegram bot credentials.",
        )
        self._labeled_entry("Bot Token (from @BotFather):", "TELEGRAM_BOT_TOKEN", show="*")
        self._nav_buttons()

    def _step_telegram_users(self) -> None:
        self._header(
            "Allowed Users",
            "Step 3 of 5 — Who can use this bot?",
        )
        self._labeled_entry(
            "Allowed User IDs (comma-separated):", "ALLOWED_USER_IDS"
        )
        self._labeled_entry("Admin User ID:", "ADMIN_USER_ID")
        self._nav_buttons()

    def _step_vault_dir(self) -> None:
        self._header(
            "Vault Directory",
            "Step 4 of 5 — Where should documents be stored?",
        )
        default = str(Path.home() / "vault")
        self._values.setdefault("VAULT_DIR", default)

        row = tk.Frame(self._frame, bg=self.BG)
        row.pack(fill="x", pady=(0, 10))

        var = tk.StringVar(value=self._values.get("VAULT_DIR", default))
        entry = tk.Entry(
            row,
            textvariable=var,
            font=self.FONT,
            bg=self.ENTRY_BG,
            fg=self.FG,
            insertbackground=self.FG,
            relief="flat",
        )
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<KeyRelease>", lambda _e: self._values.update({"VAULT_DIR": var.get()}))

        def browse() -> None:
            chosen = filedialog.askdirectory(initialdir=var.get() or str(Path.home()))
            if chosen:
                var.set(chosen)
                self._values["VAULT_DIR"] = chosen

        tk.Button(
            row,
            text="Browse…",
            font=self.FONT,
            bg=self.ENTRY_BG,
            fg=self.FG,
            relief="flat",
            command=browse,
        ).pack(side="left", padx=(6, 0))

        self._labeled_entry("Backend base URL:", "BACKEND_URL").insert(
            0, self._values.get("BACKEND_URL", "http://127.0.0.1:8000")
        )
        self._nav_buttons()

    def _step_confirm(self) -> None:
        self._header(
            "Confirm & Save",
            "Step 5 of 5 — Review your settings before saving.",
        )
        summary_lines = [
            f"Bot Token:   {'*' * 10 if self._values.get('TELEGRAM_BOT_TOKEN') else '(empty)'}",
            f"Allowed IDs: {self._values.get('ALLOWED_USER_IDS', '(empty)')}",
            f"Admin ID:    {self._values.get('ADMIN_USER_ID', '(empty)')}",
            f"Vault dir:   {self._values.get('VAULT_DIR', str(Path.home() / 'vault'))}",
            f"Backend URL: {self._values.get('BACKEND_URL', 'http://127.0.0.1:8000')}",
        ]
        tk.Label(
            self._frame,
            text="\n".join(summary_lines),
            font=("Courier New", 9),
            bg=self.BG,
            fg=self.FG,
            justify="left",
        ).pack(anchor="w", pady=10)
        self._nav_buttons(finish=True)

    # ------------------------------------------------------------------
    # .env writer
    # ------------------------------------------------------------------

    def _write_env(self) -> None:
        # SECURITY: The .env file contains sensitive credentials. Ensure it is
        # excluded from version control (.gitignore) and has restricted file
        # permissions (readable only by the current user).
        lines = [
            f"TELEGRAM_BOT_TOKEN={self._values.get('TELEGRAM_BOT_TOKEN', '')}",
            f"ALLOWED_USER_IDS={self._values.get('ALLOWED_USER_IDS', '')}",
            f"ADMIN_USER_ID={self._values.get('ADMIN_USER_ID', '')}",
            f"VAULT_DIR={self._values.get('VAULT_DIR', str(Path.home() / 'vault'))}",
            f"BACKEND_URL={self._values.get('BACKEND_URL', 'http://127.0.0.1:8000')}",
        ]
        self._env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        log.info(".env written to %s", self._env_path)

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def run(self) -> bool:
        """Block until the wizard completes. Returns True on success."""
        self._root.mainloop()
        return self._result


def run_first_run_wizard(env_path: Optional[Path] = None) -> bool:
    """Convenience wrapper: show the wizard and return True if .env was saved."""
    wizard = FirstRunWizard(env_path=env_path)
    return wizard.run()
