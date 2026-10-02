"""Sending long text and generated media back to the chat."""
from __future__ import annotations

import html
from pathlib import Path

from telegram import Bot
from telegram.error import BadRequest
from telegram.constants import ParseMode

CHUNK = 3800


def split(text: str, size: int = CHUNK) -> list[str]:
    if len(text) <= size:
        return [text]
    out, cur = [], ""
    for para in text.split("\n\n"):
        block = para if not cur else cur + "\n\n" + para
        if len(block) > size:
            if cur:
                out.append(cur)
            while len(para) > size:
                out.append(para[:size])
                para = para[size:]
            cur = para
        else:
            cur = block
    if cur:
        out.append(cur)
    return out


def esc(text: str) -> str:
    return html.escape(str(text), quote=False)


async def send_long(bot: Bot, chat_id: int, text: str) -> None:
    text = str(text or "(empty)")
    for i, part in enumerate(split(text)):
        if i == 0:
            await bot.send_message(chat_id, esc(part), parse_mode=ParseMode.HTML,
                                   disable_web_page_preview=True)
        else:
            await bot.send_message(chat_id, esc(part), parse_mode=ParseMode.HTML,
                                   disable_web_page_preview=True)


async def send_long_md(bot: Bot, chat_id: int, text: str) -> None:
    """Markdown variant: pass text through unescaped, ParseMode.MARKDOWN.
    Use for trader-console output (asterisks / backticks / underscores)."""
    text = str(text or "(empty)")
    for part in split(text):
        try:
            await bot.send_message(chat_id, part, parse_mode=ParseMode.MARKDOWN,
                                   disable_web_page_preview=True)
        except BadRequest:
            # Legacy Markdown parse failure (unbalanced * _ `) — retry plain.
            await bot.send_message(chat_id, part, disable_web_page_preview=True)


async def send_long_html(bot: Bot, chat_id: int, text: str) -> None:
    """HTML variant for text that already contains HTML tags (e.g. TRADER_HELP).
    Skips the esc() pass that would double-escape the markup."""
    text = str(text or "(empty)")
    for i, part in enumerate(split(text)):
        await bot.send_message(chat_id, part, parse_mode=ParseMode.HTML,
                               disable_web_page_preview=True)


async def deliver(bot: Bot, chat_id: int, payload: dict) -> None:
    """payload keys: photo | voice | video | document (file paths)."""
    try:
        if "photo" in payload:
            await bot.send_photo(chat_id, Path(payload["photo"]))
        elif "voice" in payload:
            await bot.send_voice(chat_id, Path(payload["voice"]))
        elif "video" in payload:
            await bot.send_video(chat_id, Path(payload["video"]))
        elif "document" in payload:
            await bot.send_document(chat_id, Path(payload["document"]))
    except Exception as e:
        await bot.send_message(chat_id, f"Could not deliver the file: {e}")
