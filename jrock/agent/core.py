"""The agent loop: LLM + tools, with soul, skills, memory and sessions."""
from __future__ import annotations

import asyncio
import json
import re

from .context import AgentContext
from .tools import build_registry

REGISTRY = build_registry()

FEEDBACK = re.compile(
    r"(?i)(remember (?:that )?|from now on|never|always|prefer|"
    r"don'?t (?:ever )?|stop (?:do|being)|correct(?:ion)?|you'?re wrong|"
    r"i meant|what i meant)")

TOOL_ROLE = "tool"


def sanitize_messages(messages: list[dict]) -> list[dict]:
    """
    Drop tool messages that no longer follow their assistant tool call.

    Replaying a session with `history[-20:]` can slice in the middle of a tool
    exchange, leaving a `role: "tool"` message whose parent assistant message
    was cut off. Gateways answer that with

        400 Message has tool role, but there was no previous assistant
            message with a tool call

    which is fatal for the whole conversation, not just that one turn. Walk the
    list and keep a tool message only when an assistant message earlier in the
    same list declared a tool call with the same id.
    """
    out: list[dict] = []
    # ids of tool calls declared by assistant messages seen so far
    declared: set[str] = set()
    for m in messages:
        role = m.get("role")
        if role == "assistant":
            for tc in m.get("tool_calls") or []:
                tid = tc.get("id")
                if tid:
                    declared.add(tid)
            out.append(m)
        elif role == TOOL_ROLE:
            tid = m.get("tool_call_id")
            # An unnamed tool message can only be trusted right after its parent;
            # ids are the only reliable link, so require one we have seen.
            if tid and tid in declared:
                out.append(m)
        else:
            out.append(m)
    return out


class Agent:
    def __init__(self, ctx: AgentContext, role: str = "general",
                 directive: str = "") -> None:
        self.ctx = ctx
        self.role = role
        self.directive = directive
        self.cancel = asyncio.Event()
        self.tools = self._pick_tools()
        self.messages: list[dict] = []
        self.task: str = ""

    def _pick_tools(self) -> dict:
        if self.role == "plan":
            keep = {"web_search", "web_fetch", "web_deepsearch", "file_read",
                    "file_list", "file_search", "memory_recall", "skill_read",
                    "memory_store"}
        elif self.role == "build":
            keep = {"terminal_run", "file_read", "file_write", "file_edit",
                    "file_list", "file_search", "web_search", "memory_recall",
                    "skill_read", "memory_store"}
        else:
            keep = set(REGISTRY)
        return {n: t for n, t in REGISTRY.items() if n in keep}

    # ------------------------------------------------------------- prompt
    def system_prompt(self, extra: str = "") -> str:
        s = self.ctx.settings
        # Report the model actually in use, not the literal "AUTO" placeholder.
        model = s.model
        if (model or "").strip().lower() in ("", "auto"):
            try:
                model = self.ctx.llm.resolve_model()
            except Exception:
                model = s.model
        parts = [
            "You are J-Rock, a full AI agent running on Telegram. You work step "
            "by step: call tools to learn and to change things, then answer.",
            self._soul_block(),
            self._skills_block(),
            self._memory_block(),
            f"<harness>\nprovider: {s.provider}\nmodel: {model}\n"
            f"thinking: {s.thinking}\n"
            f"terminal: {'enabled' if s.terminal_allowed else 'disabled'} "
            "(destructive actions need user approval unless auto-approve is on)\n"
            f"auto_approve_on_edit: {s.auto_approve_on_edit}\n"
            f"learning: {s.learning}\n</harness>",
        ]
        if self.role != "general":
            parts.append(self._role_block())
        if self.directive:
            parts.append(self.directive)
        if extra:
            parts.append(extra)
        parts.append(
            "<protocol>\n"
            "Use native tool calling when the model supports it. After each tool "
            "result, continue the task. When the task is complete, answer in "
            "plain text: lead with the answer, details after. Never invent tool "
            "results, and stop as soon as the task is done.\n</protocol>")
        return "\n\n".join(parts)

    def _soul_block(self) -> str:
        name = self.ctx.settings.default_soul
        text = self.ctx.soul.get(name) if self.ctx.soul else ""
        if not text:
            return "<soul>Be direct, kind and efficient.</soul>"
        return f'<soul name="{name}">\n{text}\n</soul>'

    def _skills_block(self) -> str:
        if not self.ctx.skills or not self.ctx.skills.names():
            return ""
        return ("<skills>\n" + self.ctx.skills.index() +
                "\nLoad a skill with skill_read(name) before doing that kind of "
                "work.\n</skills>")

    def _memory_block(self) -> str:
        if not self.ctx.memory:
            return ""
        mem = self.ctx.memory
        lines = []
        prof = mem.get_profile(self.ctx.user_id)
        if prof:
            lines.append("Profile: " + json.dumps(prof)[:500])
        facts = mem.recall(self.ctx.user_id, self.task, k=5)
        if facts:
            lines.append("Relevant memories:\n" + "\n".join(f"- {f}" for f in facts))
        lessons = mem.lessons(self.ctx.user_id, 5)
        if lessons:
            lines.append("Lessons learned (apply them):\n" +
                         "\n".join(f"- {x}" for x in lessons))
        if not lines:
            return ""
        return "<memory>\n" + "\n".join(lines) + "\n</memory>"

    def _role_block(self) -> str:
        if self.role == "plan":
            return ("<role>PLAN agent - research and design. Read-only: no writes, "
                    "no commands. Produce a concrete ordered plan with risks and "
                    "verification steps.</role>")
        if self.role == "build":
            return ("<role>BUILD agent - implement. Edit files and run commands, "
                    "then verify by running the tests or build. Keep the diff "
                    "minimal and explain what you changed.</role>")
        return ""

    # --------------------------------------------------------------- loop
    async def run(self, task: str, history: list[dict] | None = None) -> str:
        self.task = task
        self.messages = list(history or [])[-20:]
        self.messages.append({"role": "system", "content": self.system_prompt()})
        self.messages.append({"role": "user", "content": task})
        self._log("user", task)

        tool_specs = [t.spec() for t in self.tools.values()]
        if self.ctx.mcp:
            tool_specs += self.ctx.mcp.tool_specs()

        final = ""
        msg: dict = {}
        for _ in range(self.ctx.settings.max_agent_steps):
            if self.cancel.is_set():
                return final or "Stopped by /quit."
            try:
                msg = await self.ctx.llm.chat(sanitize_messages(self.messages),
                                              tools=tool_specs or None)
            except Exception as e:
                return f"Model error: {e}"
            content = msg.get("content") or ""
            calls = msg.get("tool_calls") or []
            self.messages.append({"role": "assistant", "content": content,
                                  **({"tool_calls": calls} if calls else {})})
            if content:
                self._log("assistant", content)
            if not calls:
                final = content or "(no answer)"
                break
            if content.strip():
                await self.ctx.say(f"💭 {content.strip()[:300]}")
            for tc in calls:
                name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = await self._dispatch(name, args)
                self.messages.append({"role": "tool",
                                      "tool_call_id": tc.get("id") or f"call_{name}",
                                      "name": name,
                                      "content": str(result)[:12000]})
                self._log("tool", f"{name} -> {str(result)[:300]}")
        else:
            final = "Reached the step limit. Last message:\n" + \
                    (msg.get("content") or "(none)")
        self._maybe_learn(task)
        return final

    async def _dispatch(self, name: str, args: dict) -> str:
        if name.startswith("mcp__"):
            if not self.ctx.mcp:
                return "MCP is not available."
            return await self.ctx.mcp.call(name, args, self.ctx)
        tool = self.tools.get(name)
        if not tool:
            return f"Tool '{name}' is not available to the {self.role} role."
        try:
            return await tool.handler(args, self.ctx)
        except Exception as e:
            return f"Tool error: {e}"

    def _log(self, role: str, content: str) -> None:
        if self.ctx.sessions and self.ctx.session_id:
            self.ctx.sessions.append(self.ctx.session_id, role, str(content)[:20000])

    def _maybe_learn(self, task: str) -> None:
        """User-experience learning: capture corrections and standing rules."""
        if self.ctx.settings.learning and self.ctx.memory and FEEDBACK.search(task):
            self.ctx.memory.add_lesson(self.ctx.user_id, f"User feedback: {task[:300]}")
