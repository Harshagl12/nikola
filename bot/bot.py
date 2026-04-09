"""
bot/bot.py — Telegram bot for the Nikola AI assistant.

Commands
────────
  /start        — welcome message
  /help         — list commands
  /files        — list indexed documents and sizes
  /remove <fn>  — delete a specific file from the system
  /clearall     — begin the 30-second confirmation window
  /confirmclear — execute the full wipe (only within 30 s of /clearall)
  (any other text) — forward to the backend /chat endpoint
"""

import logging
import os
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ──────────────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────────────

load_dotenv()

BOT_TOKEN: str = os.environ["TELEGRAM_BOT_TOKEN"]
BACKEND_URL: str = os.getenv("BACKEND_URL", "http://127.0.0.1:8000")
VAULT_DIR = Path(os.getenv("VAULT_DIR", str(Path.home() / "vault")))

_raw_ids = os.getenv("ALLOWED_USER_IDS", "")
ALLOWED_USER_IDS: set[int] = {
    int(x.strip()) for x in _raw_ids.split(",") if x.strip().isdigit()
}

CLEARALL_TIMEOUT = 30  # seconds

# ──────────────────────────────────────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────────────────────────────────────

LOG_DIR = Path.home() / ".nikola" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

_handler = RotatingFileHandler(
    LOG_DIR / "bot.log",
    maxBytes=10 * 1024 * 1024,
    backupCount=3,
    encoding="utf-8",
)
_handler.setFormatter(
    logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
)
logging.getLogger().addHandler(_handler)
logging.getLogger().setLevel(logging.INFO)

log = logging.getLogger("bot")

# ──────────────────────────────────────────────────────────────────────────────
# State: per-user pending clearall timestamps
# ──────────────────────────────────────────────────────────────────────────────

_pending_clearall: dict[int, float] = {}  # user_id → timestamp


# ──────────────────────────────────────────────────────────────────────────────
# Access guard
# ──────────────────────────────────────────────────────────────────────────────


def _is_allowed(update: Update) -> bool:
    uid = update.effective_user.id if update.effective_user else None
    if not ALLOWED_USER_IDS:
        return True  # open access if not configured
    return uid in ALLOWED_USER_IDS


async def _deny(update: Update) -> None:
    await update.message.reply_text("⛔ You are not authorised to use this bot.")


# ──────────────────────────────────────────────────────────────────────────────
# Backend helpers
# ──────────────────────────────────────────────────────────────────────────────


async def _get(path: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{BACKEND_URL}{path}")
        resp.raise_for_status()
        return resp.json()


async def _post(path: str, **kwargs) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{BACKEND_URL}{path}", **kwargs)
        resp.raise_for_status()
        return resp.json()


import re as _re

_SAFE_NAME_RE = _re.compile(r"^[\w.\-]{1,255}$")


def _validate_filename(filename: str) -> str:
    """Return the basename of *filename* after rejecting unsafe names."""
    import os as _os

    name = _os.path.basename(_os.path.normpath(filename))
    if not name or not _SAFE_NAME_RE.match(name) or name in (".", ".."):
        raise ValueError(f"Invalid filename: {filename!r}")
    return name


def _file_size(filename: str) -> str:
    """Return a human-readable file size string for *filename* in the vault."""
    try:
        safe_name = _validate_filename(filename)
    except ValueError:
        return "unknown"
    path = VAULT_DIR / safe_name
    if path.exists():
        size = path.stat().st_size
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"
    return "unknown"


# ──────────────────────────────────────────────────────────────────────────────
# Command handlers
# ──────────────────────────────────────────────────────────────────────────────


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        await _deny(update)
        return
    await update.message.reply_text(
        "👋 Hello! I'm *Nikola*, your AI assistant.\n\n"
        "Send me any question, or use /help to see commands.",
        parse_mode="Markdown",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        await _deny(update)
        return
    await update.message.reply_text(
        "*Nikola Commands*\n\n"
        "/files — list indexed documents\n"
        "/remove <filename> — delete a specific file\n"
        "/clearall — wipe all documents (requires confirmation)\n"
        "/confirmclear — confirm the wipe within 30 s\n"
        "/help — show this message",
        parse_mode="Markdown",
    )


async def cmd_files(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        await _deny(update)
        return
    try:
        data = await _get("/rag/files")
    except Exception as exc:
        await update.message.reply_text(f"❌ Error fetching files: {exc}")
        return

    files = data.get("files", [])
    if not files:
        await update.message.reply_text("📂 No documents indexed yet.")
        return

    lines = ["*Indexed documents:*\n"]
    for f in files:
        size_str = _file_size(f["filename"])
        lines.append(f"• `{f['filename']}` — {f['chunks']} chunks, {size_str}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        await _deny(update)
        return
    if not context.args:
        await update.message.reply_text("Usage: /remove <filename>")
        return
    filename = " ".join(context.args)
    try:
        await _post("/rag/remove", json={"filename": filename})
        await update.message.reply_text(f"✅ `{filename}` removed.", parse_mode="Markdown")
    except Exception as exc:
        await update.message.reply_text(f"❌ Error removing file: {exc}")


async def cmd_clearall(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        await _deny(update)
        return
    uid = update.effective_user.id
    _pending_clearall[uid] = time.monotonic()
    await update.message.reply_text(
        "⚠️ *Warning!* This will permanently delete ALL indexed documents and "
        "chat history.\n\n"
        f"Type /confirmclear within {CLEARALL_TIMEOUT} seconds to proceed, "
        "or do nothing to cancel.",
        parse_mode="Markdown",
    )


async def cmd_confirmclear(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if not _is_allowed(update):
        await _deny(update)
        return
    uid = update.effective_user.id
    pending_ts = _pending_clearall.get(uid)
    if pending_ts is None:
        await update.message.reply_text(
            "ℹ️ No active clear-all session. Use /clearall first."
        )
        return
    elapsed = time.monotonic() - pending_ts
    _pending_clearall.pop(uid, None)
    if elapsed > CLEARALL_TIMEOUT:
        await update.message.reply_text(
            "⏰ Confirmation window expired. Please run /clearall again."
        )
        return
    try:
        await _post("/rag/clear-all")
        await update.message.reply_text("🧹 All data wiped successfully.")
    except Exception as exc:
        await update.message.reply_text(f"❌ Error during clear-all: {exc}")


# ──────────────────────────────────────────────────────────────────────────────
# General message → /chat
# ──────────────────────────────────────────────────────────────────────────────


async def handle_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if not _is_allowed(update):
        await _deny(update)
        return
    user_text = update.message.text or ""
    uid = str(update.effective_user.id)

    # Typing indicator while waiting
    await update.message.chat.send_action("typing")

    try:
        # Stream the response and accumulate
        reply_chunks: list[str] = []
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream(
                "POST",
                f"{BACKEND_URL}/chat",
                json={"message": user_text, "session_id": uid},
            ) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_text():
                    reply_chunks.append(chunk)

        reply = "".join(reply_chunks).strip() or "🤔 No response from the AI."
    except Exception as exc:
        log.error("Chat error: %s", exc)
        reply = f"❌ Error contacting AI backend: {exc}"

    # Telegram messages are limited to 4096 chars; split if needed
    for i in range(0, len(reply), 4096):
        await update.message.reply_text(reply[i : i + 4096])


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────


def main() -> None:
    log.info("Bot starting…")
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("files", cmd_files))
    app.add_handler(CommandHandler("remove", cmd_remove))
    app.add_handler(CommandHandler("clearall", cmd_clearall))
    app.add_handler(CommandHandler("confirmclear", cmd_confirmclear))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    log.info("Polling…")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
