"""Approval buttons for dangerous tool calls (terminal, file edits)."""
from __future__ import annotations

import asyncio
import uuid

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler

TIMEOUT = 300.0


class Approvals:
    def __init__(self) -> None:
        self._pending: dict[str, asyncio.Future] = {}

    async def request(self, bot: Bot, chat_id: int, what: str) -> bool:
        token = uuid.uuid4().hex[:12]
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[token] = fut
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Approve", callback_data=f"appr:{token}"),
            InlineKeyboardButton("❌ Deny", callback_data=f"deny:{token}"),
        ]])
        await bot.send_message(
            chat_id,
            f"🔐 Approval needed:\n\n<pre>{what[:1500]}</pre>",
            reply_markup=kb)
        try:
            return await asyncio.wait_for(fut, timeout=TIMEOUT)
        except asyncio.TimeoutError:
            return False
        finally:
            self._pending.pop(token, None)

    def handler(self) -> CallbackQueryHandler:
        async def on_callback(update, context):
            query = update.callback_query
            action, _, token = query.data.partition(":")
            fut = self._pending.get(token)
            if fut is None or fut.done():
                await query.answer("This request expired.", show_alert=True)
                return
            fut.set_result(action == "appr")
            await query.answer("Approved." if action == "appr" else "Denied.")
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass
        return CallbackQueryHandler(on_callback)
