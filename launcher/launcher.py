"""
launcher.py — Entry point for the Nikola Windows launcher.

Execution order
───────────────
1.  Single-instance lock (TCP socket on port 47821)
2.  Splash screen (shown while background thread runs setup)
3.  Ollama check + serve
4.  Model pull (llama3.1:8b, moondream2, nomic-embed-text)
5.  Venv creation + pip installs for backend and bot
6.  Node_modules check + npm install for Electron
7.  Vault directory creation
8.  First-run wizard (if .env absent)
9.  Launch FastAPI, Telegram bot, Electron
10. System-tray icon + watchdog
"""

import logging
import os
import socket
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

# ──────────────────────────────────────────────────────────────────────────────
# Paths (resolved relative to the frozen .exe or the script location)
# ──────────────────────────────────────────────────────────────────────────────

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent.parent.resolve()

LAUNCHER_DIR = BASE_DIR / "launcher"
BACKEND_DIR = BASE_DIR / "backend"
BOT_DIR = BASE_DIR / "bot"
ELECTRON_DIR = BASE_DIR / "electron"
ENV_FILE = BASE_DIR / ".env"
VAULT_DIR = Path.home() / "vault"
LOG_DIR = Path.home() / ".nikola" / "logs"

BACKEND_VENV = BASE_DIR / ".venv_backend"
BOT_VENV = BASE_DIR / ".venv_bot"

LOCK_PORT = 47821

# ──────────────────────────────────────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────────────────────────────────────

LOG_DIR.mkdir(parents=True, exist_ok=True)
_root_log = logging.getLogger()
_root_log.setLevel(logging.DEBUG)

_file_handler = RotatingFileHandler(
    LOG_DIR / "launcher.log",
    maxBytes=10 * 1024 * 1024,
    backupCount=3,
    encoding="utf-8",
)
_file_handler.setFormatter(
    logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
)
_root_log.addHandler(_file_handler)

log = logging.getLogger("launcher")

# ──────────────────────────────────────────────────────────────────────────────
# Single-instance lock
# ──────────────────────────────────────────────────────────────────────────────


def _acquire_lock() -> socket.socket:
    """
    Bind a TCP socket to LOCK_PORT.  Raises SystemExit if another
    instance is already running.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    try:
        sock.bind(("127.0.0.1", LOCK_PORT))
        sock.listen(1)
        return sock
    except OSError:
        try:
            import tkinter as tk
            from tkinter import messagebox

            root = tk.Tk()
            root.withdraw()
            messagebox.showwarning(
                "Nikola Already Running",
                "Another instance of Nikola is already running.\n"
                "Check the system tray.",
            )
            root.destroy()
        except Exception:
            pass
        sys.exit(0)


# ──────────────────────────────────────────────────────────────────────────────
# Fatal-error helper
# ──────────────────────────────────────────────────────────────────────────────


def _fatal(title: str, message: str) -> None:
    """Show a tkinter error dialog, log the error, then exit."""
    log.critical("%s — %s", title, message)
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(title, message)
        root.destroy()
    except Exception:
        pass
    sys.exit(1)


# ──────────────────────────────────────────────────────────────────────────────
# Background setup (runs in worker thread while splash is visible)
# ──────────────────────────────────────────────────────────────────────────────

_ollama_started_by_us = False


def _setup(splash, done_event: threading.Event) -> None:
    """
    Long-running setup sequence.  Reports progress to *splash*.
    Sets *done_event* when complete (success or fatal error).
    """
    global _ollama_started_by_us

    from dep_installer import (
        ensure_models,
        ensure_node_modules,
        ensure_vault,
        install_backend_deps,
        install_bot_deps,
        is_ollama_installed,
        is_ollama_running,
        prompt_install_ollama,
        start_ollama_serve,
        create_venv,
    )

    try:
        # ── Step 1: Ollama check ──────────────────────────────────────
        splash.update("Checking Ollama…", 10)
        if not is_ollama_installed():
            prompt_install_ollama()
            _fatal(
                "Ollama Required",
                "Ollama must be installed before running Nikola.\n"
                "Please install it and restart.",
            )

        # ── Step 2: Ollama serve ──────────────────────────────────────
        splash.update("Starting Ollama…", 18)
        if not is_ollama_running():
            start_ollama_serve()
            _ollama_started_by_us = True
            # Wait up to 10 s for the server to come up
            for _ in range(20):
                time.sleep(0.5)
                if is_ollama_running():
                    break
            else:
                _fatal("Ollama", "Ollama server did not start in time.")

        # ── Step 3: Model pull ────────────────────────────────────────
        splash.update("Checking AI models…", 26)
        ensure_models(
            progress_cb=lambda model, line: splash.update(
                f"Pulling {model}…", 34
            )
        )

        # ── Step 4: Vault directory ───────────────────────────────────
        splash.update("Setting up vault…", 42)
        ensure_vault(VAULT_DIR)

        # ── Step 5: Backend venv + deps ───────────────────────────────
        splash.update("Installing backend dependencies…", 50)
        create_venv(BACKEND_VENV)
        install_backend_deps(BACKEND_VENV)

        # ── Step 6: Bot venv + deps ───────────────────────────────────
        splash.update("Installing bot dependencies…", 62)
        create_venv(BOT_VENV)
        install_bot_deps(BOT_VENV)

        # ── Step 7: Node modules ──────────────────────────────────────
        if ELECTRON_DIR.exists():
            splash.update("Checking Electron dependencies…", 72)
            ensure_node_modules(ELECTRON_DIR)

        # ── Step 8: First-run wizard (if .env absent) ─────────────────
        if not ENV_FILE.exists():
            splash.update("Opening first-run wizard…", 80)
            # Signal splash to close so the wizard can take focus
            done_event.set()
            # Block here until wizard finishes
            from first_run import run_first_run_wizard

            if not run_first_run_wizard(ENV_FILE):
                _fatal("Setup Cancelled", "First-run setup is required.")
            # Re-open splash briefly (best-effort)
            done_event.clear()

        splash.update("Launching processes…", 90)

    except SystemExit:
        raise
    except Exception as exc:
        log.exception("Setup failed")
        _fatal("Nikola Setup Error", str(exc))
    finally:
        done_event.set()


# ──────────────────────────────────────────────────────────────────────────────
# Process commands
# ──────────────────────────────────────────────────────────────────────────────


def _python(venv: Path) -> str:
    """Return the Python executable path for *venv*."""
    if sys.platform == "win32":
        return str(venv / "Scripts" / "python.exe")
    return str(venv / "bin" / "python")


def _build_processes(pm) -> None:
    """Register backend, bot, and Electron in the ProcessManager."""
    from process_manager import ManagedProcess

    # Load .env variables into the child-process environment.
    # python-dotenv handles quoting, escaping, and multi-line values correctly.
    env = os.environ.copy()
    if ENV_FILE.exists():
        try:
            from dotenv import dotenv_values

            env.update(
                {k: v for k, v in dotenv_values(ENV_FILE).items() if v is not None}
            )
        except ImportError:
            # Fallback: simple line-by-line parsing (no quoting support)
            for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()

    backend = ManagedProcess(
        name="backend",
        cmd=[
            _python(BACKEND_VENV),
            "-m",
            "uvicorn",
            "main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
        ],
        cwd=BACKEND_DIR,
        env=env,
        log_dir=LOG_DIR,
    )

    bot = ManagedProcess(
        name="bot",
        cmd=[_python(BOT_VENV), "bot.py"],
        cwd=BOT_DIR,
        env=env,
        log_dir=LOG_DIR,
    )

    pm.add(backend)
    pm.add(bot)

    if ELECTRON_DIR.exists():
        electron = ManagedProcess(
            name="electron",
            cmd=["npx", "electron", "."],
            cwd=ELECTRON_DIR,
            env=env,
            log_dir=LOG_DIR,
        )
        pm.add(electron)


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────


def main() -> None:
    lock_socket = _acquire_lock()
    log.info("Nikola launcher starting (BASE_DIR=%s)", BASE_DIR)

    from splash import SplashScreen
    from process_manager import ProcessManager
    from tray import TrayIcon

    splash = SplashScreen()
    splash.update("Initialising…", 5)

    done_event = threading.Event()
    setup_thread = threading.Thread(
        target=_setup, args=(splash, done_event), daemon=True, name="setup"
    )
    setup_thread.start()

    # Pump Tk events until setup is done
    splash.run_until_done(done_event)

    # ── Start processes ───────────────────────────────────────────────
    pm = ProcessManager(log_dir=LOG_DIR)
    _build_processes(pm)
    pm.start_all()

    # ── System tray ───────────────────────────────────────────────────
    def _quit() -> None:
        log.info("Quit requested from tray.")
        pm.stop_all()
        if _ollama_started_by_us:
            import subprocess

            subprocess.run(["ollama", "stop"], check=False)
        lock_socket.close()
        sys.exit(0)

    tray = TrayIcon(
        icon_path=BASE_DIR / "nikola_icon.ico",
        vault_path=VAULT_DIR,
        quit_callback=_quit,
        status_callback=pm.status,
    )
    tray.run()

    log.info("Nikola is running. Tray icon active.")

    # Keep the main thread alive (the watchdog and tray run in daemon threads)
    try:
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        _quit()


if __name__ == "__main__":
    main()
