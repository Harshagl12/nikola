"""
tray.py — System-tray icon for Nikola using pystray.

Menu items:
  • Status         — shows a summary messagebox
  • Open Vault     — opens the vault folder in Explorer
  • ──────────────
  • Quit           — graceful shutdown callback
"""

import os
import sys
import threading
import logging
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger(__name__)

try:
    import pystray
    from pystray import MenuItem as Item
    from PIL import Image, ImageDraw
except ImportError:
    pystray = None  # type: ignore[assignment]
    Image = None  # type: ignore[assignment]


def _build_icon_image(size: int = 64) -> "Image.Image":
    """Generate a simple placeholder icon if no .ico file is present."""
    img = Image.new("RGBA", (size, size), (10, 10, 20, 255))
    draw = ImageDraw.Draw(img)
    # Blue filled circle
    margin = size // 8
    draw.ellipse(
        [margin, margin, size - margin, size - margin],
        fill=(85, 85, 255, 255),
    )
    # White "N" letter placeholder
    draw.text((size // 3, size // 4), "N", fill=(255, 255, 255, 255))
    return img


class TrayIcon:
    """
    Wraps a pystray.Icon and runs it in a dedicated daemon thread so
    the rest of the application (and the Tk main loop) stay responsive.
    """

    def __init__(
        self,
        icon_path: Optional[Path] = None,
        vault_path: Optional[Path] = None,
        quit_callback: Optional[Callable[[], None]] = None,
        status_callback: Optional[Callable[[], dict]] = None,
    ) -> None:
        if pystray is None:
            log.warning("pystray not installed — tray icon disabled.")
            return

        self._vault_path = vault_path or (Path.home() / "vault")
        self._quit_cb = quit_callback or (lambda: None)
        self._status_cb = status_callback or (lambda: {})
        self._icon: Optional[pystray.Icon] = None

        # Load icon image
        if icon_path and icon_path.exists():
            image = Image.open(icon_path)
        else:
            image = _build_icon_image()

        menu = pystray.Menu(
            Item("Nikola", lambda: None, enabled=False),
            pystray.Menu.SEPARATOR,
            Item("Status", self._on_status),
            Item("Open Vault", self._on_open_vault),
            pystray.Menu.SEPARATOR,
            Item("Quit", self._on_quit),
        )
        self._icon = pystray.Icon("Nikola", image, "Nikola AI", menu)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Start the tray icon event loop in a daemon thread."""
        if self._icon is None:
            return
        t = threading.Thread(target=self._icon.run, daemon=True, name="tray")
        t.start()
        log.info("Tray icon started.")

    def stop(self) -> None:
        """Remove the tray icon."""
        if self._icon:
            try:
                self._icon.stop()
            except Exception as exc:
                log.debug("Tray stop error: %s", exc)

    # ------------------------------------------------------------------
    # Menu handlers
    # ------------------------------------------------------------------

    def _on_status(self, icon, item) -> None:  # noqa: ANN001
        status = self._status_cb()
        lines = [f"{'✓' if alive else '✗'}  {name}" for name, alive in status.items()]
        msg = "\n".join(lines) if lines else "No managed processes."
        self._show_info("Nikola — Process Status", msg)

    def _on_open_vault(self, icon, item) -> None:  # noqa: ANN001
        try:
            if sys.platform == "win32":
                os.startfile(str(self._vault_path))
            elif sys.platform == "darwin":
                import subprocess

                subprocess.Popen(["open", str(self._vault_path)])
            else:
                import subprocess

                subprocess.Popen(["xdg-open", str(self._vault_path)])
        except Exception as exc:
            log.error("Could not open vault: %s", exc)

    def _on_quit(self, icon, item) -> None:  # noqa: ANN001
        self.stop()
        self._quit_cb()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _show_info(title: str, message: str) -> None:
        """Display a non-blocking messagebox using tkinter."""
        try:
            import tkinter as tk
            from tkinter import messagebox

            root = tk.Tk()
            root.withdraw()
            messagebox.showinfo(title, message)
            root.destroy()
        except Exception as exc:
            log.debug("messagebox error: %s", exc)
