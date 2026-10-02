"""Entry point: wires everything and starts Telegram polling."""
from __future__ import annotations

import sys
import traceback

from telegram import Bot, BotCommand
from telegram.ext import Application, ContextTypes

from .agent.core import Agent
from .agent.mcp import MCPManager
from .agent.memory import Memory
from .agent.sessions import SessionStore
from .agent.skills import SkillStore
from .agent.soul import SoulStore
from .bot import handlers
from .bot.confirm import Approvals
from .config import Settings
from .llm.client import LLMClient


class AppCore:
    def __init__(self) -> None:
        self.settings = Settings.load()
        self.llm = LLMClient(self.settings)
        self.memory = Memory()
        self.sessions = SessionStore()
        self.souls = SoulStore()
        self.skills = SkillStore()
        self.mcp = MCPManager()
        self.approvals = Approvals()
        self.running: dict[int, Agent] = {}
        self.active_session: dict[int, str] = {}
        self.resume: dict[int, bool] = {}
        self.bot: Bot | None = None
        self.bot_data: dict = {}


async def _post_init(app: Application) -> None:
    core: AppCore = app.bot_data["core"]
    core.bot = app.bot
    core.bot_data = app.bot_data
    # Init unified store (creates tables if they don't exist)
    from .store import init_schema
    await init_schema()
    from .bot.handlers import BOT_COMMANDS
    if BOT_COMMANDS:
        await core.bot.set_my_commands([
            BotCommand(command=c, description=d) for c, d in BOT_COMMANDS])
    if core.mcp.servers:
        report = await core.mcp.connect_all()
        print("[mcp] " + report.replace("\n", " | "))
    print(f"[jrock] ready as {app.bot.username} | "
          f"{core.settings.provider}/{core.settings.model} | "
          f"soul={core.settings.default_soul} | "
          f"users={core.settings.tg_allowed_ids or 'everyone'}")
    # Bring the engine and the reporter back if they were running when we last
    # stopped. Without this, every restart silently dropped Telegram reporting
    # even though `report_on` was still saved as true.
    try:
        from .trader_handler import restore_after_boot
        await restore_after_boot(app.bot, app.bot_data)
    except Exception as e:
        print(f"[jrock] boot restore skipped: {type(e).__name__}: {e}")


async def _on_error(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Report handler failures instead of letting PTB swallow them.

    Without an error handler, any exception raised inside a handler reaches the
    asyncio loop's default handler, which logs "No error handlers are
    registered" and drops it. The bot stays up, keeps polling, and the user
    gets no reply at all — indistinguishable from a hang.
    """
    exc = context.error
    # Full traceback to stdout so Railway shows the real stack, not just the
    # exception name. PTB's default handler only prints the name.
    print("[error] unhandled in handler:", repr(exc))
    traceback.print_exception(type(exc), exc, exc.__traceback__)
    # Tell the user something is wrong instead of leaving them in the dark.
    try:
        chat_id = None
        if update is not None and getattr(update, "effective_chat", None) is not None:
            chat_id = update.effective_chat.id
        if chat_id is not None and context.bot is not None:
            await context.bot.send_message(chat_id, f"⚠️ error: {type(exc).__name__}")
    except Exception:
        pass


def main() -> None:
    core = AppCore()
    if not core.settings.tg_token:
        print("TG_BOT_TOKEN is missing. Copy .env.example to .env and fill it in.")
        sys.exit(1)
    app = Application.builder().token(core.settings.tg_token).post_init(_post_init) \
                             .build()
    app.bot_data["core"] = core
    app.add_error_handler(_on_error)
    handlers.register(app, core)
    print("[jrock] starting polling…")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
