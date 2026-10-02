from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from ..config import Settings
from ..llm.client import LLMClient

Notify = Callable[[str], Awaitable[None]]
Confirm = Callable[[str], Awaitable[bool]]
Deliver = Callable[[dict], Awaitable[None]]  # send generated file/photo to the chat


@dataclass
class AgentContext:
    """Everything a tool or sub-agent needs. Built once in main.py."""
    settings: Settings
    llm: LLMClient
    user_id: int
    chat_id: int
    notify: Optional[Notify] = None
    confirm: Optional[Confirm] = None
    deliver: Optional[Deliver] = None
    chat: Any = None                 # python-telegram-bot Bot for file sending
    workspace: Any = None            # Path: where terminal/fs tools operate
    memory: Any = None               # jrock.agent.memory.Memory
    sessions: Any = None             # jrock.agent.sessions.SessionStore
    mcp: Any = None                  # jrock.agent.mcp.MCPManager
    skills: Any = None               # jrock.agent.skills.SkillStore
    soul: Any = None                 # jrock.agent.soul.SoulStore
    session_id: str = ""
    bot_data: Any = None             # telegram app.bot_data (holds the trader engine)
    user_profile: dict = field(default_factory=dict)

    async def say(self, text: str) -> None:
        if self.notify:
            await self.notify(text)

    async def ask_permission(self, what: str) -> bool:
        """Gate destructive actions. Honours /auto_approve_on_edit."""
        if self.settings.auto_approve_on_edit:
            return True
        if self.confirm is None:
            return True
        return await self.confirm(what)
