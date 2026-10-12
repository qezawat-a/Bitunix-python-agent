"""Gateway failures are exercised without credentials or live API calls."""
import asyncio
import json
import time
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from jrock.config import Settings
from jrock.llm import client as llm


MODELS = ["ag/claude-opus-4-6-thinking", "ag/claude-sonnet-5-5",
          "bzl/gpt-5.4", "cl/chat"]
TOOLS = [{"type": "function", "function": {"name": "example", "parameters": {}}}]
MESSAGE = {"role": "assistant", "content": "Hello!"}


class ModelFailoverTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.settings = Settings(provider="custom", model="", api_keys={
            "local_base_url": "https://gateway.invalid/v1/", "custom": "test-key",
        })
        self.client = llm.LLMClient(self.settings)
        self.models = list(MODELS)
        self.requests = []
        self.list_calls = 0
        self.respond = lambda model: httpx.Response(200, json={"choices": [{"message": MESSAGE}]})
        real_client = httpx.AsyncClient
        self.real_client = real_client
        transport = httpx.MockTransport(self.handle)
        self.addCleanup(patch.stopall)
        patch.object(llm.httpx, "AsyncClient", side_effect=lambda **kw: real_client(
            transport=transport, **kw)).start()

    async def handle(self, request):
        self.assertEqual(request.url.host, "gateway.invalid")
        if request.method == "GET":
            self.list_calls += 1
            self.assertEqual(request.url.path, "/v1/models")
            return httpx.Response(200, json={"data": [{"id": m} for m in self.models]})
        body = json.loads(request.content)
        self.requests.append(body)
        response = self.respond(body["model"])
        return await response if asyncio.iscoroutine(response) else response

    async def chat(self, **kwargs):
        return await self.client.chat([{"role": "user", "content": "Hi"}], **kwargs)

    async def test_nested_403_fails_over_to_other_route_and_caches_success(self):
        def respond(model):
            if model.startswith("ag/"):
                return httpx.Response(503, json={"error": {"message":
                    "[antigravity/claude-opus-4-6-thinking] [403]: HTTP 403 (reset after 2m)"}})
            return httpx.Response(200, json={"choices": [{"message": MESSAGE}]})
        self.respond = respond
        progress = AsyncMock()
        self.assertEqual(await self.chat(tools=TOOLS, on_progress=progress), MESSAGE)
        self.assertEqual([r["model"] for r in self.requests], [MODELS[0], MODELS[2]])
        self.assertTrue(all(r["tools"] == TOOLS for r in self.requests))
        self.assertEqual(self.client._model_cache["custom"], MODELS[2])
        progress.assert_awaited_once()
        self.assertIn(MODELS[2], progress.call_args.args[0])
        self.assertGreater(self.client._route_cooldowns["custom", "ag"], time.monotonic())
        await self.chat()
        self.assertEqual(self.requests[-1]["model"], MODELS[2])
        self.assertEqual(self.list_calls, 1)
        self.assertEqual(self.client.resolve_model(), MODELS[2])

    async def test_cached_success_is_evicted_after_later_failure(self):
        await self.chat()
        self.assertEqual(self.client._model_cache["custom"], MODELS[0])
        self.respond = lambda model: httpx.Response(503) if model == MODELS[0] else httpx.Response(
            200, json={"choices": [{"message": MESSAGE}]})
        await self.chat()
        self.assertEqual(self.client._model_cache["custom"], MODELS[2])

    async def test_explicit_selection_does_not_switch_models(self):
        self.settings.model = MODELS[0]
        self.respond = lambda _: httpx.Response(503, text="unavailable")
        with self.assertRaisesRegex(llm.LLMError, "HTTP 503"):
            await self.chat()
        self.assertEqual([r["model"] for r in self.requests], [MODELS[0]])
        self.assertEqual(self.list_calls, 0)

    async def test_invalid_gateway_credentials_are_not_retried(self):
        for status in (401, 403):
            with self.subTest(status=status):
                self.requests.clear()
                self.respond = lambda _: httpx.Response(status, text="invalid credentials")
                with self.assertRaisesRegex(llm.LLMError, f"HTTP {status}"):
                    await self.chat()
                self.assertEqual(len(self.requests), 1)

    async def test_bad_request_is_not_retried(self):
        self.respond = lambda _: httpx.Response(400, text="unsupported tools")
        with self.assertRaisesRegex(llm.LLMError, "HTTP 400"):
            await self.chat()
        self.assertEqual(len(self.requests), 1)

    async def test_retry_limit_and_cooldown_prevent_repeated_failed_requests(self):
        self.models = [MODELS[0], MODELS[2], MODELS[3]]
        self.respond = lambda _: httpx.Response(503, text="unavailable")
        with self.assertRaisesRegex(llm.LLMError, "after 3 model attempts"):
            await self.chat()
        self.assertEqual(len(self.requests), 3)
        with self.assertRaisesRegex(llm.LLMError, "cooldown"):
            await self.chat()
        self.assertEqual(len(self.requests), 3)
        self.assertNotIn("custom", self.client._model_cache)

    async def test_rate_limited_model_uses_retry_after(self):
        self.respond = lambda model: httpx.Response(429, headers={"Retry-After": "180"}) \
            if model == MODELS[0] else httpx.Response(200, json={"choices": [{"message": MESSAGE}]})
        before = time.monotonic()
        await self.chat()
        self.assertGreaterEqual(self.client._cooldowns["custom", MODELS[0]], before + 180)

    async def test_hanging_model_is_cancelled_before_failover(self):
        cancelled = asyncio.Event()

        async def respond(model):
            if model == MODELS[0]:
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
            return httpx.Response(200, json={"choices": [{"message": MESSAGE}]})

        self.respond = respond
        with patch.object(llm, "AUTO_ATTEMPT_TIMEOUT", 0.02):
            answer = await asyncio.wait_for(self.chat(), 1)
        self.assertEqual(answer, MESSAGE)
        self.assertTrue(cancelled.is_set())
        self.assertEqual([r["model"] for r in self.requests], [MODELS[0], MODELS[2]])

    async def test_request_cancellation_does_not_start_another_model(self):
        started = asyncio.Event()

        async def respond(model):
            started.set()
            await asyncio.Event().wait()

        self.respond = respond
        task = asyncio.create_task(self.chat())
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(len(self.requests), 1)
        self.assertFalse(self.client._cooldowns)

    async def test_discovery_is_bounded(self):
        async def blocked(request):
            await asyncio.Event().wait()

        transport = httpx.MockTransport(blocked)
        # Bypass the setUp factory while keeping all requests in memory.
        with patch.object(llm.httpx, "AsyncClient", side_effect=lambda **kw:
                          self.real_client(transport=transport, **kw)), \
                patch.object(llm, "DISCOVERY_TIMEOUT", 0.02):
            with self.assertRaisesRegex(llm.LLMError, "/models timed out"):
                await asyncio.wait_for(self.chat(), 1)

    async def test_catalog_is_not_truncated_before_alternative_route(self):
        self.models = [f"ag/claude-opus-{i:03d}" for i in range(210)] + ["zz/chat"]
        self.respond = lambda model: httpx.Response(503, text="HTTP 403 (reset after 2m)") \
            if model.startswith("ag/") else httpx.Response(200, json={"choices": [{"message": MESSAGE}]})
        self.assertEqual(await self.chat(), MESSAGE)
        self.assertEqual(self.requests[-1]["model"], "zz/chat")

    async def test_api_key_change_invalidates_success_and_failure_caches(self):
        await self.chat()
        self.client._cooldowns["custom", MODELS[0]] = time.monotonic() + 100
        self.settings.api_keys["custom"] = "new-test-key"
        self.models = ["new/chat"]
        await self.chat()
        self.assertEqual(self.requests[-1]["model"], "new/chat")
        self.assertEqual(self.list_calls, 2)
        self.assertFalse(self.client._cooldowns)

    async def test_tool_only_completion_is_success(self):
        message = {"role": "assistant", "content": None,
                   "tool_calls": [{"id": "call1", "type": "function",
                                   "function": {"name": "example", "arguments": "{}"}}]}
        self.respond = lambda _: httpx.Response(200, json={"choices": [{"message": message}]})
        self.assertEqual(await self.chat(tools=TOOLS), message)
        self.assertEqual(len(self.requests), 1)

    async def test_empty_response_fails_over_instead_of_claiming_success(self):
        self.respond = lambda model: httpx.Response(200, json={"choices": [{"message":
            {"role": "assistant", "content": ""} if model == MODELS[0] else MESSAGE}]})
        self.assertEqual(await self.chat(), MESSAGE)
        self.assertEqual(len(self.requests), 2)
