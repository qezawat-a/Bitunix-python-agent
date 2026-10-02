"""Async LLM client with tool-calling + thinking controls.

Works with any OpenAI-compatible endpoint. Auto-detects available models.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
import asyncio
from typing import Any, Optional

import httpx

from ..config import Settings
from .providers import PROVIDERS, provider_meta

TIMEOUT = 300.0

# AUTO / empty AI_MODEL means "pick a model that actually works on this key".
AUTO_TOKENS = {"", "auto", "none"}
WORKING_REPORT = Path(__file__).resolve().parent.parent.parent / "data" / "working_models.json"
REPORT_TTL = 24 * 3600

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


def _load_working_models() -> list[str]:
    """Read cached working models if fresh."""
    try:
        if time.time() - WORKING_REPORT.stat().st_mtime > REPORT_TTL:
            return []
        data = json.loads(WORKING_REPORT.read_text())
        return [r["model"] for r in data.get("results", []) if r.get("ok")]
    except Exception:
        return []


def pick_model(working: list[str]) -> str:
    """Choose from working set by preference, else first available."""
    if not working:
        return ""
    for candidate in AUTO_PREFERENCE:
        if candidate in working:
            return candidate
    return working[0]


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self._model_cache: dict[str, str] = {}  # provider -> resolved model

    # ------------------------------------------------------------------ meta
    def _endpoint(self, provider: str | None = None, model: str | None = None) -> tuple[str, dict]:
        provider = provider or self.s.provider
        meta = provider_meta(provider)
        base = meta["base_url"]
        if provider == "local":
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
        return base, headers

    async def list_models(self, provider: str | None = None) -> list[str]:
        """Fetch available models from /models endpoint."""
        provider = provider or self.s.provider
        try:
            base, headers = self._endpoint(provider)
            url = f"{base}/models"
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.get(url, headers=headers)
                if r.status_code == 200:
                    data = parse_json_body(r.text)
                    ids = [m.get("id") for m in data.get("data", []) if m.get("id")]
                    if ids:
                        return sorted(ids)[:200]
        except Exception:
            pass
        return provider_meta(provider).get("models", [])

    async def _test_model(self, base: str, headers: dict, model: str, timeout: int = 8) -> bool:
        """Quick test: can this model accept a chat request?"""
        try:
            body = {
                "model": model,
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 10,
                "temperature": 0.5,
            }
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(f"{base}/chat/completions", headers=headers, json=body)
            # 400 auth errors or missing model errors are expected; other errors may succeed
            return r.status_code < 500
        except asyncio.TimeoutError:
            return False
        except Exception:
            return False

    def resolve_model(self, model: str | None = None,
                      provider: str | None = None) -> str:
        """Sync fallback: resolve model using cached data."""
        provider = provider or self.s.provider
        model = (model or self.s.model or "").strip()
        if model.lower() not in AUTO_TOKENS:
            return model
        if provider != "custom":
            presets = provider_meta(provider).get("models", [])
            return presets[0] if presets else ""
        return pick_model(_load_working_models())

    async def resolve_model_async(self, model: str | None = None,
                                   provider: str | None = None) -> str:
        """
        Async: query /models and pick the first available model.
        
        For custom providers, tries to find a working model by testing.
        Falls back to sync resolution if discovery fails.
        """
        provider = provider or self.s.provider
        model = (model or self.s.model or "").strip()
        
        # Non-AUTO model: use as-is
        if model.lower() not in AUTO_TOKENS:
            return model
        
        # Check cache
        if provider in self._model_cache:
            cached = self._model_cache[provider]
            if cached:
                return cached
        
        try:
            base, headers = self._endpoint(provider)
            # Get available models from /models endpoint
            models = await self.list_models(provider)
            if models:
                # For custom providers, test each until one works
                if provider == "custom":
                    for candidate in models[:10]:  # Test first 10
                        if await self._test_model(base, headers, candidate):
                            self._model_cache[provider] = candidate
                            return candidate
                else:
                    # Non-custom: just pick first
                    self._model_cache[provider] = models[0]
                    return models[0]
        except Exception:
            pass
        
        # Final fallback
        fallback = self.resolve_model(model, provider)
        if fallback:
            self._model_cache[provider] = fallback
        return fallback

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
    ) -> dict:
        """One chat completion. Returns the raw response message dict."""
        provider = provider or self.s.provider
        model = await self.resolve_model_async(model, provider)
        
        if not model:
            raise LLMError(
                f"No model available for {provider}. "
                f"Check AI_BASE_URL, AI_API_KEY, and verify /models endpoint works."
            )
        
        thinking = thinking or self.s.thinking
        base, headers = self._endpoint(provider, model)
        meta = provider_meta(provider)

        if meta.get("auth") == "anthropic":
            return await self._chat_anthropic(base, headers, model, messages,
                                              tools, thinking, temperature, max_tokens)

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
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.post(url, headers=headers, json=body)
        except httpx.TimeoutException:
            raise LLMError(
                f"{provider}/{model} -> timed out after {int(TIMEOUT)}s. "
                "The gateway is slow or overloaded; try again or pick another model."
            )
        except httpx.RequestError as e:
            raise LLMError(f"{provider}/{model} -> cannot reach {base}: {e}")
        if r.status_code >= 400:
            raise LLMError(
                f"{provider}/{model} -> HTTP {r.status_code}: "
                f"{clean_error(r.status_code, r.text)}"
            )
        data = parse_json_body(r.text)
        try:
            return data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            raise LLMError(
                f"{provider}/{model} -> malformed reply: {clean_error(200, r.text)}"
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
