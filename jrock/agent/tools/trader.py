"""Let the agent read/change the trader's settings from plain chat
("set breakeven to 15", "turn trailing trigger to 20", "what's my risk?").

Money-affecting keys (risk, leverage, paper mode, symbol, margin/position
mode, account TP/SL, max positions) ALWAYS need a tap on the Approve button,
even when /auto_approve_on_edit is on.
"""
from __future__ import annotations

from types import SimpleNamespace

from config import Config

from ..context import AgentContext

RISKY = {
    "paper", "risk", "leverage", "max", "max_positions", "margin", "margin_mode",
    "position_mode", "account_tp", "account_sl", "symbol", "symbols",
}


async def get_settings(args: dict, ctx: AgentContext) -> str:
    if not Config.is_admin(ctx.user_id):
        return "Owner only."
    from ... import store
    rows = await store.kv_all("t/")
    if not rows:
        return "No trader settings saved yet (defaults in use)."
    return "\n".join(f"{k[2:]} = {v}" for k, v in rows.items())


async def set_setting(args: dict, ctx: AgentContext) -> str:
    if not Config.is_admin(ctx.user_id):
        return "Owner only."
    key = str(args.get("key", "")).strip().lower().replace(" ", "_")
    val = str(args.get("value", "")).strip()
    if not key or not val:
        return "Need both key and value."
    if key in RISKY:
        if ctx.confirm is None or not await ctx.confirm(
                f"Change trader setting: {key} = {val}"):
            return f"Not changed: {key} (approval denied or timed out)."
    from ... import trader_handler
    fake_update = SimpleNamespace(
        effective_user=SimpleNamespace(id=ctx.user_id),
        effective_chat=SimpleNamespace(id=ctx.chat_id),
    )
    fake_ctx = SimpleNamespace(bot_data=ctx.bot_data if ctx.bot_data is not None else {},
                               bot=ctx.chat)
    return await trader_handler._set(f"{key} {val}", fake_update, fake_ctx)
