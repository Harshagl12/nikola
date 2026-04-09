"""
dep_installer.py — dependency checks and silent installation helpers.

All operations run in-process or via subprocesses with CREATE_NO_WINDOW
so no console windows flash on screen.
"""

import os
import sys
import shutil
import subprocess
import urllib.request
import webbrowser
import logging
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

_NO_WINDOW = 0x08000000  # subprocess.CREATE_NO_WINDOW (Windows only)


def _run(cmd: list[str], cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
    """Run *cmd* without a console window; capture output."""
    flags = _NO_WINDOW if sys.platform == "win32" else 0
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=flags,
        check=False,
    )


def _pip(venv_dir: Path, packages: list[str]) -> None:
    """Install *packages* into *venv_dir* silently."""
    pip_exe = venv_dir / ("Scripts/pip.exe" if sys.platform == "win32" else "bin/pip")
    result = _run([str(pip_exe), "install", "--quiet", *packages])
    if result.returncode != 0:
        raise RuntimeError(
            f"pip install failed:\n{result.stderr.decode(errors='replace')}"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Ollama
# ──────────────────────────────────────────────────────────────────────────────

OLLAMA_DOWNLOAD_URL = "https://ollama.com/download"
REQUIRED_MODELS = ["llama3.1:8b", "moondream2", "nomic-embed-text"]


def is_ollama_installed() -> bool:
    """Return True when the *ollama* executable is discoverable on PATH."""
    return shutil.which("ollama") is not None


def prompt_install_ollama() -> None:
    """
    Show a tkinter dialog asking the user to install Ollama manually,
    then open the download page in the default browser.
    """
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showwarning(
            "Ollama Not Found",
            "Ollama is required but was not found on this system.\n\n"
            "Please install Ollama from:\n"
            f"{OLLAMA_DOWNLOAD_URL}\n\n"
            "After installation, restart Nikola.",
        )
        root.destroy()
    except Exception:
        pass
    webbrowser.open(OLLAMA_DOWNLOAD_URL)


def is_ollama_running() -> bool:
    """Return True if the Ollama HTTP API is already listening."""
    try:
        with urllib.request.urlopen(
            "http://127.0.0.1:11434/api/tags", timeout=2
        ) as resp:
            return resp.status == 200
    except Exception:
        return False


def start_ollama_serve() -> "subprocess.Popen[bytes]":
    """
    Start *ollama serve* as a background process.
    Returns the Popen handle so the caller can manage lifetime.
    """
    flags = _NO_WINDOW if sys.platform == "win32" else 0
    proc = subprocess.Popen(
        ["ollama", "serve"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )
    log.info("ollama serve started (pid=%d)", proc.pid)
    return proc


def pull_model(model: str, progress_cb: Optional[Callable[[str], None]] = None) -> None:
    """
    Pull *model* using `ollama pull`.  Streams output lines to *progress_cb*.
    """
    flags = _NO_WINDOW if sys.platform == "win32" else 0
    with subprocess.Popen(
        ["ollama", "pull", model],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=flags,
    ) as proc:
        for raw in proc.stdout:
            line = raw.decode(errors="replace").rstrip()
            log.debug("ollama pull %s: %s", model, line)
            if progress_cb:
                progress_cb(line)
        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"Failed to pull model '{model}'")


def ensure_models(
    models: list[str] = REQUIRED_MODELS,
    progress_cb: Optional[Callable[[str, str], None]] = None,
) -> None:
    """
    Ensure every model in *models* is available locally.
    *progress_cb(model, line)* receives streaming pull output.
    """
    result = _run(["ollama", "list"])
    existing = result.stdout.decode(errors="replace")
    for model in models:
        short = model.split(":")[0]
        if short not in existing and model not in existing:
            log.info("Pulling model: %s", model)
            pull_model(
                model,
                progress_cb=lambda line, m=model: (
                    progress_cb(m, line) if progress_cb else None
                ),
            )
        else:
            log.info("Model already present: %s", model)


# ──────────────────────────────────────────────────────────────────────────────
# Virtual environments
# ──────────────────────────────────────────────────────────────────────────────

BACKEND_REQUIREMENTS = [
    "fastapi",
    "uvicorn[standard]",
    "chromadb",
    "langchain",
    "langchain-community",
    "python-multipart",
    "python-dotenv",
    "httpx",
]

BOT_REQUIREMENTS = [
    "python-telegram-bot>=20.0",
    "httpx",
    "python-dotenv",
]


def create_venv(venv_dir: Path) -> None:
    """Create a Python virtual environment at *venv_dir* if absent."""
    if not venv_dir.exists():
        log.info("Creating venv at %s", venv_dir)
        result = _run([sys.executable, "-m", "venv", str(venv_dir)])
        if result.returncode != 0:
            raise RuntimeError(
                f"venv creation failed:\n{result.stderr.decode(errors='replace')}"
            )


def install_backend_deps(venv_dir: Path) -> None:
    """Install backend Python requirements into *venv_dir*."""
    log.info("Installing backend dependencies…")
    _pip(venv_dir, BACKEND_REQUIREMENTS)


def install_bot_deps(venv_dir: Path) -> None:
    """Install Telegram bot requirements into *venv_dir*."""
    log.info("Installing bot dependencies…")
    _pip(venv_dir, BOT_REQUIREMENTS)


# ──────────────────────────────────────────────────────────────────────────────
# Vault directory
# ──────────────────────────────────────────────────────────────────────────────


def ensure_vault(vault_dir: Optional[Path] = None) -> Path:
    """Create and return the vault directory (default: ~/vault)."""
    target = vault_dir or Path.home() / "vault"
    target.mkdir(parents=True, exist_ok=True)
    log.info("Vault directory: %s", target)
    return target


# ──────────────────────────────────────────────────────────────────────────────
# Node / Electron
# ──────────────────────────────────────────────────────────────────────────────


def ensure_node_modules(electron_dir: Path) -> None:
    """Run `npm install` inside *electron_dir* if node_modules is absent."""
    node_modules = electron_dir / "node_modules"
    if not node_modules.exists():
        log.info("Running npm install in %s", electron_dir)
        result = _run(["npm", "install"], cwd=electron_dir)
        if result.returncode != 0:
            raise RuntimeError(
                f"npm install failed:\n{result.stderr.decode(errors='replace')}"
            )
