"""
process_manager.py — subprocess lifecycle management for Nikola.

Starts FastAPI backend, Telegram bot, and Electron sidebar with
CREATE_NO_WINDOW; redirects their stdout/stderr to rotating log
files; and monitors them every 30 s, restarting crashed processes.
"""

import os
import sys
import time
import logging
import threading
import subprocess
from pathlib import Path
from logging.handlers import RotatingFileHandler
from typing import Optional

log = logging.getLogger(__name__)

_NO_WINDOW = 0x08000000  # subprocess.CREATE_NO_WINDOW


def _make_rotating_handler(log_path: Path, name: str) -> RotatingFileHandler:
    log_path.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_path / f"{name}.log",
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    return handler


class ManagedProcess:
    """
    A subprocess that is automatically restarted if it exits unexpectedly.
    """

    def __init__(
        self,
        name: str,
        cmd: list[str],
        cwd: Optional[Path] = None,
        env: Optional[dict] = None,
        log_dir: Optional[Path] = None,
    ) -> None:
        self.name = name
        self.cmd = cmd
        self.cwd = cwd
        self.env = env
        self._log_dir = log_dir or (Path.home() / ".nikola" / "logs")
        self._proc: Optional[subprocess.Popen] = None
        self._log_handler = _make_rotating_handler(self._log_dir, name)
        self._logger = logging.getLogger(f"proc.{name}")
        self._logger.addHandler(self._log_handler)
        self._logger.setLevel(logging.DEBUG)
        self._reader: Optional[threading.Thread] = None
        self._stopped = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Spawn the process and begin reading its output."""
        self._stopped = False
        flags = _NO_WINDOW if sys.platform == "win32" else 0
        self._proc = subprocess.Popen(
            self.cmd,
            cwd=str(self.cwd) if self.cwd else None,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=flags,
        )
        log.info("[%s] started (pid=%d)", self.name, self._proc.pid)
        self._reader = threading.Thread(
            target=self._drain_output, daemon=True, name=f"reader-{self.name}"
        )
        self._reader.start()

    def stop(self) -> None:
        """Terminate the process gracefully, then forcefully if needed."""
        self._stopped = True
        if self._proc and self._proc.poll() is None:
            log.info("[%s] stopping (pid=%d)…", self.name, self._proc.pid)
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            except Exception as exc:
                log.warning("[%s] error during stop: %s", self.name, exc)

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def is_stopped(self) -> bool:
        """Return True if this process was intentionally stopped."""
        return self._stopped

    def restart(self) -> None:
        """Stop (if alive) then re-start."""
        self.stop()
        time.sleep(1)
        self.start()

    # ------------------------------------------------------------------
    # Output draining
    # ------------------------------------------------------------------

    def _drain_output(self) -> None:
        """Read stdout/stderr from the child and write to the rotating log."""
        try:
            for raw in self._proc.stdout:
                line = raw.decode(errors="replace").rstrip()
                self._logger.info(line)
        except Exception as exc:
            log.debug("[%s] output reader exited: %s", self.name, exc)


class ProcessManager:
    """
    Manages a collection of ManagedProcess instances and runs a watchdog
    thread that checks every 30 s and restarts any crashed processes.
    """

    def __init__(self, log_dir: Optional[Path] = None) -> None:
        self._processes: dict[str, ManagedProcess] = {}
        self._log_dir = log_dir or (Path.home() / ".nikola" / "logs")
        self._watchdog: Optional[threading.Thread] = None
        self._running = False

    # ------------------------------------------------------------------
    # Process registration & lifecycle
    # ------------------------------------------------------------------

    def add(self, proc: ManagedProcess) -> None:
        self._processes[proc.name] = proc

    def start_all(self) -> None:
        for proc in self._processes.values():
            try:
                proc.start()
            except Exception as exc:
                log.error("Failed to start %s: %s", proc.name, exc)
        self._running = True
        self._watchdog = threading.Thread(
            target=self._watch_loop, daemon=True, name="watchdog"
        )
        self._watchdog.start()

    def stop_all(self) -> None:
        self._running = False
        for proc in self._processes.values():
            proc.stop()

    # ------------------------------------------------------------------
    # Watchdog
    # ------------------------------------------------------------------

    def _watch_loop(self) -> None:
        while self._running:
            time.sleep(30)
            if not self._running:
                break
            for proc in list(self._processes.values()):
                if not proc.is_stopped() and not proc.is_running():
                    log.warning("[watchdog] %s has crashed — restarting…", proc.name)
                    try:
                        proc.start()
                    except Exception as exc:
                        log.error("[watchdog] failed to restart %s: %s", proc.name, exc)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self) -> dict[str, bool]:
        return {name: p.is_running() for name, p in self._processes.items()}
