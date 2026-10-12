"""Async LLM client with tool-calling + thinking controls.

Works with any OpenAI-compatible endpoint. Auto-detects available models.
"""
from __future__ import annotations

import json
import time
import asyncio
import hashlib
import re
from typing import Any, Awaitable, Callable, Optional

import httpx

from ..config import Settings
from .providers import provider_meta

TIMEOUT = 60.0
AUTO_ATTEMPT_TIMEOUT = 30.0
MAX_AUTO_ATTEMPTS = 3
DISCOVERY_TIMEOUT = 10.0
CATALOG_TTL = 300.0
FAILURE_COOLDOWN = 120.0

# AUTO / empty AI_MODEL means "pick a model that actually works on this key".
AUTO_TOKENS = {"", "auto", "none"}

# Preference order for AUTO picks
AUTO_PREFERENCE = (
    "gpt-4o",
    "gpt-4-turbo",
    "gpt-4",
    "claude-opus",
    "claude-sonnet",
    "gemini-2.5-pro",
    "llama-3.3-70b",
)


def parse_json_body(text: str) -> dict:
    """Parse a response that may carry an SSE trailer."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        obj, _ = json.JSONDecoder().raw_decode(text.lstrip())
        return obj


def clean_error(status: int, text: str) -> str:
    """
    Turn a response body into a one-line reason.

    A gateway that times out (502/503/504) or sits behind CloudFront answers
    with a full HTML error page. Passing that through to Telegram produces a
    wall of markup, so pull the <title>/<h1> out and keep it short.
    """
    body = text.strip()
    if body.startswith("<"):
        for tag in ("<H1>", "<H2>", "<TITLE>"):
            start = body.upper().find(tag)
            if start != -1:
                end = body.find("<", start + len(tag))
                if end != -1:
                    return body[start + len(tag):end].strip()
        return "upstream gateway returned an HTML error page"
    data = None
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        pass
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            return str(err.get("message") or data.get("message") or err)[:300]
        if err:
            return str(err)[:300]
    return body[:300]


def pick_model(working: list[str]) -> str:
    """Choose from working set by preference, else first available."""
    if not working:
        return ""
    for candidate in AUTO_PREFERENCE:
        if candidate in working:
            return candidate
    # Gateways commonly expose provider-qualified IDs such as
    # ``ag/claude-sonnet-5-5``.  Prefer those by family when the short
    # preference name is not an exact ID.
    lowered = [(model, model.lower()) for model in working]
    for candidate in AUTO_PREFERENCE:
        needle = candidate.lower()
        match = next((model for model, value in lowered if needle in value), None)
        if match:
            return match
    return working[0]


class LLMError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False,
                 cooldown: float = FAILURE_COOLDOWN, route_blocked: bool = False):
        super().__init__(message)
        self.retryable = retryable
        self.cooldown = cooldown
        self.route_blocked = route_blocked


class LLMClient:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self._model_cache: dict[str, str] = {}  # provider -> resolved model
        self._catalog: dict[str, tuple[float, list[str]]] = {}
        self._cooldowns: dict[tuple[str, str], float] = {}
        self._route_cooldowns: dict[tuple[str, str], float] = {}
        self._scopes: dict[str, tuple[str, str]] = {}
        self._discovery_errors: dict[str, str] = {}

    # ------------------------------------------------------------------ meta
    def _endpoint(self, provider: str | None = None, model: str | None = None) -> tuple[str, dict]:
        provider = provider or self.s.provider
        meta = provider_meta(provider)
        base = meta["base_url"]
        if provider in ("local", "custom"):
            base = self.s.api_keys.get("local_base_url", base)
        if provider == "anthropic":
            base = "https://api.anthropic.com"
        headers: dict[str, str] = {"Content-Type": "application/json"}
        key = self.s.api_keys.get(provider, "")
        if not key:
            key = self.s.api_keys.get("API_KEY", "")
        if meta.get("auth") == "anthropic":
            headers["x-api-key"] = key
            headers["anthropic-version"] = "2023-06-01"
        else:
            if key:
                headers["Authorization"] = f"Bearer {key}"
        return base.rstrip("/"), headers

    def _check_scope(self, provider: str) -> None:
        """Never reuse model availability across gateway/key changes."""
        base, headers = self._endpoint(provider)
        fingerprint = hashlib.sha256(json.dumps(headers, sort_keys=True).encode()).hexdigest()
        scope = (base, fingerprint)
        if self._scopes.get(provider) != scope:
            self._scopes[provider] = scope
            self._model_cache.pop(provider, None)
            self._catalog.pop(provider, None)
            self._discovery_errors.pop(provider, None)
            self._cooldowns = {k: v for k, v in self._cooldowns.items() if k[0] != provider}
            self._route_cooldowns = {
                k: v for k, v in self._route_cooldowns.items() if k[0] != provider
            }

    @staticmethod
    def _route(model: str) -> str:
        return model.split("/", 1)[0] if "/" in model else ""

    def _eligible(self, provider: str, model: str) -> bool:
        now = time.monotonic()
        return (bool(model) and self._cooldowns.get((provider, model), 0) <= now
                and self._route_cooldowns.get((provider, self._route(model)), 0) <= now)

    def _next_model(self, provider: str, attempted: list[str]) -> str:
        models = self._catalog.get(provider, (0, []))[1]
        available = [m for m in models if m not in attempted and self._eligible(provider, m)]
        # A gateway route can have many aliases backed by the same account.
        # Prefer a different route before spending another attempt on it.
        routes = {self._route(m) for m in attempted}
        diverse = [m for m in available if self._route(m) not in routes]
        return pick_model(diverse or available)

    async def list_models(self, provider: str | None = None) -> list[str]:
        """Fetch available models from /models endpoint."""
        provider = provider or self.s.provider
        self._check_scope(provider)
        try:
            base, headers = self._endpoint(provider)
            url = f"{base}/models"
            async with httpx.AsyncClient(timeout=DISCOVERY_TIMEOUT) as client:
                r = await asyncio.wait_for(client.get(url, headers=headers), DISCOVERY_TIMEOUT)
                if r.status_code == 200:
                    data = parse_json_body(r.text)
                    ids = [m.get("id") for m in data.get("data", []) if m.get("id")]
                    if ids:
                        models = sorted(set(ids))
                        self._catalog[provider] = (time.monotonic(), models)
                        self._discovery_errors.pop(provider, None)
                        return models
                    self._discovery_errors[provider] = "/models returned an empty catalog"
                else:
                    self._discovery_errors[provider] = (
                        f"/models -> HTTP {r.status_code}: {clean_error(r.status_code, r.text)}"
                    )
        except (asyncio.TimeoutError, httpx.TimeoutException):
            self._discovery_errors[provider] = f"/models timed out after {DISCOVERY_TIMEOUT:g}s"
        except httpx.RequestError as e:
            self._discovery_errors[provider] = f"/models is unreachable ({type(e).__name__})"
        except (ValueError, TypeError, AttributeError):
            self._discovery_errors[provider] = "/models returned an invalid catalog"
        return self._catalog.get(provider, (0, provider_meta(provider).get("models", [])))[1]

    def resolve_model(self, model: str | None = None,
                      provider: str | None = None) -> str:
        """Sync fallback: resolve model using cached data."""
        provider = provider or self.s.provider
        self._check_scope(provider)
        model = (self.s.model if model is None else model) or ""
        model = model.strip()
        if model.lower() not in AUTO_TOKENS:
            return model
        cached = self._model_cache.get(provider, "")
        if self._eligible(provider, cached):
            return cached
        selected = self._next_model(provider, [])
        if selected:
            return selected
        if provider != "custom":
            presets = provider_meta(provider).get("models", [])
            return presets[0] if presets else ""
        return ""

    async def resolve_model_async(self, model: str | None = None,
                                   provider: str | None = None) -> str:
        """Select a listed model without probes; only chat success is cached."""
        provider = provider or self.s.provider
        self._check_scope(provider)
        model = ((self.s.model if model is None else model) or "").strip()
        
        # Non-AUTO model: use as-is
        if model.lower() not in AUTO_TOKENS:
            return model
        
        stamp, models = self._catalog.get(provider, (0, []))
        if not models or time.monotonic() - stamp >= CATALOG_TTL:
            models = await self.list_models(provider)
        if provider not in self._catalog and models:
            self._catalog[provider] = (time.monotonic(), models)
        cached = self._model_cache.get(provider, "")
        if cached in models and self._eligible(provider, cached):
            return cached
        return self._next_model(provider, [])

    # ----------------------------------------------------------------- chat
    async def chat(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        *,
        provider: str | None = None,
        model: str | None = None,
        thinking: str | None = None,
        temperature: float = 0.4,
        max_tokens: int = 4096,
        on_progress: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> dict:
        """One chat completion. Returns the raw response message dict."""
        provider = provider or self.s.provider
        requested = ((self.s.model if model is None else model) or "").strip()
        auto = provider == "custom" and requested.lower() in AUTO_TOKENS
        model = await self.resolve_model_async(model, provider)
        
        if not model:
            if self._catalog.get(provider, (0, []))[1]:
                raise LLMError("Listed models are temporarily unavailable (cooldown). "
                               "Try after the gateway cooldown or use /set model <id>.")
            reason = self._discovery_errors.get(provider, "No models returned")
            raise LLMError(
                f"No model available for {provider}: {reason}. "
                "Check gateway availability, AI_BASE_URL and AI_API_KEY."
            )
        
        attempts: list[str] = []
        failures: list[str] = []
        for _ in range(MAX_AUTO_ATTEMPTS if auto else 1):
            attempts.append(model)
            try:
                message = await self._chat_once(
                    messages, tools, provider=provider, model=model,
                    thinking=thinking or self.s.thinking, temperature=temperature,
                    max_tokens=max_tokens, timeout=AUTO_ATTEMPT_TIMEOUT if auto else TIMEOUT,
                )
            except LLMError as e:
                if not auto or not e.retryable:
                    if not auto and e.retryable and provider == "custom":
                        raise LLMError(f"{e}. Use /set model auto to enable failover, "
                                       "or /set model <id> to select another route.") from e
                    raise
                self._cooldowns[provider, model] = time.monotonic() + e.cooldown
                if e.route_blocked and self._route(model):
                    self._route_cooldowns[provider, self._route(model)] = (
                        time.monotonic() + e.cooldown
                    )
                if self._model_cache.get(provider) == model:
                    self._model_cache.pop(provider, None)
                failures.append(str(e))
                candidate = self._next_model(provider, attempts)
                if not candidate or len(attempts) >= MAX_AUTO_ATTEMPTS:
                    break
                if on_progress:
                    await on_progress(f"Model `{model}` unavailable; trying `{candidate}`.")
                model = candidate
            else:
                if auto:
                    self._model_cache[provider] = model
                return message
        raise LLMError(
            f"No reply after {len(attempts)} model attempts. Failed models are cooling down. "
            "Use /set model <id> to select a route. Last error: " + failures[-1]
        )

    async def _chat_once(self, messages, tools, *, provider, model, thinking,
                         temperature, max_tokens, timeout) -> dict:
        base, headers = self._endpoint(provider, model)
        meta = provider_meta(provider)

        if meta.get("auth") == "anthropic":
            try:
                return await asyncio.wait_for(
                    self._chat_anthropic(base, headers, model, messages,
                                         tools, thinking, temperature, max_tokens), timeout)
            except (asyncio.TimeoutError, httpx.TimeoutException) as e:
                raise LLMError(f"{provider}/{model} -> timed out after {timeout:g}s") from e

        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = tools
            if not meta.get("supports_tool_calls") and self.s.auto_compat:
                body.pop("tools")
        
        if thinking != "off" and meta.get("supports_thinking"):
            effort = thinking if thinking in ("low", "medium", "high") else "medium"
            if provider in ("openai", "google"):
                body["reasoning_effort"] = effort
            elif provider == "deepseek":
                body["reasoning_effort"] = "high" if effort in ("medium", "high") else effort

        url = f"{base}/chat/completions"
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await asyncio.wait_for(
                    client.post(url, headers=headers, json=body), timeout)
        except (asyncio.TimeoutError, httpx.TimeoutException):
            raise LLMError(
                f"{provider}/{model} -> timed out after {timeout:g}s.", retryable=True,
            )
        except httpx.RequestError as e:
            raise LLMError(f"{provider}/{model} -> cannot reach gateway: {e}", retryable=True)
        if r.status_code >= 400:
            reason = clean_error(r.status_code, r.text)
            cooldown = FAILURE_COOLDOWN
            reset = re.search(r"reset after\s+(\d+)\s*([ms])", reason, re.I)
            if reset:
                cooldown = int(reset[1]) * (60 if reset[2].lower() == "m" else 1)
            try:
                cooldown = float(r.headers.get("Retry-After", cooldown))
            except ValueError:
                pass
            raise LLMError(
                f"{provider}/{model} -> HTTP {r.status_code}: "
                f"{reason}", retryable=r.status_code in (404, 408, 429) or r.status_code >= 500,
                cooldown=max(10, min(cooldown, 600)),
                route_blocked=r.status_code >= 500 and bool(re.search(r"\b403\b", reason)),
            )
        try:
            data = parse_json_body(r.text)
            message = data["choices"][0]["message"]
            if not isinstance(message, dict) or not (message.get("content") or message.get("tool_calls")):
                raise ValueError("empty message")
            return message
        except (KeyError, IndexError, TypeError, ValueError):
            raise LLMError(
                f"{provider}/{model} -> empty or malformed reply: {clean_error(200, r.text)}",
                retryable=True,
            )

    async def _chat_anthropic(self, base, headers, model, messages, tools,
                              thinking, temperature, max_tokens) -> dict:
        system = ""
        convo = []
        for m in messages:
            if m.get("role") == "system":
                system = (system + "\n" + str(m.get("content", ""))).strip()
            elif m.get("role") == "assistant":
                convo.append({"role": "assistant",
                              "content": m.get("content") or ""})
            else:
                convo.append({"role": "user", "content": m.get("content") or ""})
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": convo,
        }
        if system:
            body["system"] = system
        if tools:
            body["tools"] = [
                {"name": t["function"]["name"],
                 "description": t["function"].get("description", ""),
                 "input_schema": t["function"].get("parameters", {"type": "object", "properties": {}})}
                for t in tools
            ]
        if thinking != "off":
            budget = {"low": 1024, "medium": 4096, "high": 10240}.get(thinking, 4096)
            body["thinking"] = {"type": "enabled", "budget_tokens": budget}
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.post(f"{base}/v1/messages", headers=headers, json=body)
        if r.status_code >= 400:
            raise LLMError(f"anthropic/{model} -> HTTP {r.status_code}: {r.text[:500]}")
        data = parse_json_body(r.text)
        out: dict[str, Any] = {"role": "assistant", "content": ""}
        for block in data.get("content", []):
            if block.get("type") == "text":
                out["content"] += block.get("text", "")
            elif block.get("type") == "tool_use":
                out.setdefault("tool_calls", []).append(
                    {"function": {"name": block.get("name", ""),
                                  "arguments": json.dumps(block.get("input", {}))}}
                )
        return out
