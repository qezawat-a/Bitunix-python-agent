import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from jrock.agent import core as agent_core
from jrock.agent.context import AgentContext
from jrock.bot import handlers
from jrock.config import Settings
from jrock import trader_handler


class AgentWaitTests(unittest.IsolatedAsyncioTestCase):
    def make_agent(self):
        ctx = AgentContext(settings=Settings(learning=False),
                           llm=SimpleNamespace(chat=AsyncMock()), user_id=1, chat_id=1,
                           notify=AsyncMock())
        return agent_core.Agent(ctx)

    async def test_hi_produces_real_model_reply_with_system_prompt_first(self):
        agent = self.make_agent()
        agent.ctx.llm.chat.return_value = {"content": "Hi, how can I help?"}
        result = await agent.run("hi", history=[{"role": "user", "content": "earlier"}])
        self.assertEqual(result, "Hi, how can I help?")
        agent.ctx.llm.chat.assert_awaited_once()
        messages = agent.ctx.llm.chat.call_args.args[0]
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[-1], {"role": "user", "content": "hi"})

    async def test_unavailable_memory_does_not_prevent_greeting(self):
        agent = self.make_agent()

        async def blocked(*args):
            await asyncio.Event().wait()

        agent.ctx.memory = SimpleNamespace(get_profile=blocked)
        agent.ctx.llm.chat.return_value = {"content": "Hello"}
        with patch.object(agent_core, "MEMORY_TIMEOUT", 0.02):
            self.assertEqual(await asyncio.wait_for(agent.run("hi"), 1), "Hello")

    async def test_quit_cancels_in_flight_model_wait(self):
        agent = self.make_agent()
        started, cancelled = asyncio.Event(), asyncio.Event()

        async def blocked(*args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        agent.ctx.llm.chat.side_effect = blocked
        task = asyncio.create_task(agent.run("hi"))
        await asyncio.wait_for(started.wait(), 1)
        agent.cancel.set()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        self.assertTrue(cancelled.is_set())

    async def test_wait_has_progress_and_terminal_timeout(self):
        agent = self.make_agent()

        async def blocked(*args, **kwargs):
            await asyncio.Event().wait()

        agent.ctx.llm.chat.side_effect = blocked
        with patch.object(agent_core, "MODEL_WAIT_TIMEOUT", 0.05), \
                patch.object(agent_core, "PROGRESS_INTERVAL", 0.01):
            answer = await asyncio.wait_for(agent.run("hi"), 1)
        self.assertIn("time limit", answer)
        self.assertTrue(agent.ctx.notify.await_count)
        self.assertIn("Still waiting", agent.ctx.notify.call_args.args[0])


class TelegramRoutingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot = SimpleNamespace(send_message=AsyncMock())
        self.message = SimpleNamespace(text="hi", caption=None, document=None, photo=None,
                                       reply_text=AsyncMock(), get_bot=lambda: self.bot)
        self.update = SimpleNamespace(effective_user=SimpleNamespace(id=1),
                                      effective_chat=SimpleNamespace(id=1),
                                      effective_message=self.message)
        self.core = SimpleNamespace(
            settings=Settings(learning=False), bot=self.bot,
            llm=SimpleNamespace(chat=AsyncMock(return_value={"content": "Hello!"})),
            sessions=MagicMock(), active_session={1: "test-session"}, resume={}, running={},
            memory=None, approvals=MagicMock(), mcp=None, skills=None, souls=None, bot_data={},
        )
        self.tasks = []

        def create_task(coro, **kwargs):
            task = asyncio.create_task(coro)
            self.tasks.append(task)
            return task

        self.context = SimpleNamespace(application=SimpleNamespace(
            bot_data={"core": self.core}, create_task=create_task))

    async def asyncTearDown(self):
        for task in self.tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def test_message_handler_delivers_answer_and_releases_active_agent(self):
        await handlers.on_message(self.update, self.context)
        await asyncio.wait_for(asyncio.gather(*self.tasks), 1)
        self.message.reply_text.assert_awaited_once_with("🧠 Working on it…")
        self.assertIn("Hello!", self.bot.send_message.call_args.args[1])
        self.assertFalse(self.core.running)
        self.core.llm.chat.assert_awaited_once()

    async def test_followup_reports_active_wait_instead_of_overwriting_task(self):
        started = asyncio.Event()

        async def blocked(*args, **kwargs):
            started.set()
            await asyncio.Event().wait()

        self.core.llm.chat.side_effect = blocked
        await handlers.on_message(self.update, self.context)
        await asyncio.wait_for(started.wait(), 1)
        original = self.core.running[1]
        self.message.text = "Are you there?"
        await handlers.on_message(self.update, self.context)
        self.assertIs(self.core.running[1], original)
        self.assertEqual(len(self.tasks), 1)
        self.assertIn("waiting for model", self.message.reply_text.call_args.args[0])
        await handlers.cmd_quit(self.update, self.context)
        await asyncio.wait_for(asyncio.gather(*self.tasks), 1)
        self.assertFalse(self.core.running)
        self.assertTrue(any("Stopped by /quit" in c.args[1]
                            for c in self.bot.send_message.call_args_list))

    async def test_signal_responds_without_database_while_model_is_waiting(self):
        engine = SimpleNamespace(
            _running=True,
            cfg=SimpleNamespace(quote_precision=6, account_tp=0, account_sl=0, margin_coin="USDT"),
            signal_snapshot=lambda: {"symbol": "WUSDT", "running": True,
                                     "paper": False, "timeframes": ["1m"]},
        )
        context = SimpleNamespace(bot_data={"trader_engine": engine})
        with patch.object(trader_handler, "_load_cfg", new_callable=AsyncMock) as load:
            load.side_effect = AssertionError("/signal must not query the database")
            self.assertIn("WUSDT", await trader_handler._signal("", self.update, context))
            engine._running = False
            self.assertIn("Engine stopped", await trader_handler._signal("", self.update, context))
            load.assert_not_awaited()
