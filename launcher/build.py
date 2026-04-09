"""
build.py — Build script for the Nikola Windows launcher.

1. Generates a multi-resolution nikola_icon.ico (16, 32, 48, 64, 128, 256 px).
2. Runs PyInstaller to produce a single-file windowed .exe.

Run with:  python launcher/build.py
Requires:  pip install pillow pyinstaller
"""

import subprocess
import sys
from pathlib import Path

LAUNCHER_DIR = Path(__file__).parent.resolve()
PROJECT_ROOT = LAUNCHER_DIR.parent

ICO_PATH = PROJECT_ROOT / "nikola_icon.ico"
ENTRY_POINT = LAUNCHER_DIR / "launcher.py"
EXE_NAME = "Nikola"

ICON_SIZES = [16, 32, 48, 64, 128, 256]


# ──────────────────────────────────────────────────────────────────────────────
# Icon generation
# ──────────────────────────────────────────────────────────────────────────────


def generate_icon(output_path: Path) -> None:
    """
    Create a multi-resolution .ico file using Pillow.
    If nikola_icon.png exists next to this script it will be used as the
    source image; otherwise a programmatic placeholder is generated.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("Pillow not found — run:  pip install pillow", file=sys.stderr)
        sys.exit(1)

    source_png = PROJECT_ROOT / "nikola_icon.png"

    if source_png.exists():
        base = Image.open(source_png).convert("RGBA")
    else:
        # Programmatic placeholder: dark bg + blue circle + "N"
        base = Image.new("RGBA", (256, 256), (10, 10, 20, 255))
        draw = ImageDraw.Draw(base)
        draw.ellipse([20, 20, 236, 236], fill=(85, 85, 255, 255))
        try:
            font = ImageFont.truetype("arial.ttf", 140)
        except Exception:
            font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), "N", font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(
            ((256 - tw) / 2 - bbox[0], (256 - th) / 2 - bbox[1]),
            "N",
            fill=(255, 255, 255, 255),
            font=font,
        )

    images = [base.resize((s, s), Image.LANCZOS) for s in ICON_SIZES]
    images[0].save(
        output_path,
        format="ICO",
        sizes=[(s, s) for s in ICON_SIZES],
        append_images=images[1:],
    )
    print(f"Icon written → {output_path}")


# ──────────────────────────────────────────────────────────────────────────────
# PyInstaller build
# ──────────────────────────────────────────────────────────────────────────────


def run_pyinstaller(entry: Path, icon: Path, name: str) -> None:
    """Invoke PyInstaller with the correct flags."""
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onefile",
        "--windowed",
        f"--icon={icon}",
        f"--name={name}",
        "--hidden-import=pystray._win32",
        "--collect-all=pystray",
        "--collect-all=PIL",
        # Keep launcher isolated from backend/bot imports
        "--exclude-module=fastapi",
        "--exclude-module=uvicorn",
        "--exclude-module=chromadb",
        "--exclude-module=telegram",
        str(entry),
    ]
    print("Running:", " ".join(cmd))
    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    if result.returncode != 0:
        print("PyInstaller failed.", file=sys.stderr)
        sys.exit(result.returncode)
    print(f"\nBuild complete → dist/{name}.exe")


# ──────────────────────────────────────────────────────────────────────────────
# Entry
# ──────────────────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    generate_icon(ICO_PATH)
    run_pyinstaller(ENTRY_POINT, ICO_PATH, EXE_NAME)
