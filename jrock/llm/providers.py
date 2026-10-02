"""Adds 'custom' provider that reads AI_BASE_URL + AI_API_KEY from env.
Extends the existing PROVIDERS dict from the same module.
"""
from __future__ import annotations

import json
import os

PROVIDERS: dict[str, dict] = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "key_env": "OPENAI_API_KEY",
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini",
                   "o3-mini", "o4-mini", "gpt-5"],
        "supports_tool_calls": True,
        "supports_thinking": True,
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "key_env": "DEEPSEEK_API_KEY",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "supports_tool_calls": True,
        "supports_thinking": True,
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "key_env": "GROQ_API_KEY",
        "models": ["llama-3.3-70b-versatile", "gemma2-9b-it",
                   "deepseek-r1-distill-llama-70b"],
        "supports_tool_calls": True,
        "supports_thinking": False,
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "key_env": "OPENROUTER_API_KEY",
        "models": ["openai/gpt-4o", "anthropic/claude-sonnet-4",
                   "google/gemini-2.5-pro", "deepseek/deepseek-r1",
                   "meta-llama/llama-3.3-70b-instruct"],
        "supports_tool_calls": True,
        "supports_thinking": False,
    },
    "google": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "key_env": "GOOGLE_API_KEY",
        "models": ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash"],
        "supports_tool_calls": True,
        "supports_thinking": True,
    },
    "anthropic": {
        "base_url": "https://api.anthropic.com/v1",
        "key_env": "ANTHROPIC_API_KEY",
        "models": ["claude-sonnet-4", "claude-opus-4", "claude-3-7-haiku"],
        "supports_tool_calls": True,
        "supports_thinking": True,
        "auth": "anthropic",
    },
    "local": {
        "base_url": "http://localhost:11434/v1",   # override via LOCAL_BASE_URL
        "key_env": "",
        "models": ["llama3", "qwen2.5-coder"],
        "supports_tool_calls": True,
        "supports_thinking": False,
    },
    # ── custom: arbitrary base URL from env (AI_BASE_URL + AI_API_KEY) ─────
    "custom": {
        "base_url": os.getenv("AI_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
        "key_env": "AI_API_KEY",
        "models": [],           # populated at runtime via /models discovery
        "supports_tool_calls": True,
        "supports_thinking": True,
    },
}


def provider_meta(name: str) -> dict:
    meta = dict(PROVIDERS.get(name, PROVIDERS["custom"]))
    meta["name"] = name
    # refresh custom base_url from env every call (in case /set_api_key changed it)
    if name == "custom":
        meta["base_url"] = os.getenv("AI_BASE_URL", meta["base_url"]).rstrip("/")
    return meta


def list_models_raw(name: str) -> list[str]:
    return provider_meta(name).get("models", [])


def default_model(name: str, current: str = "") -> str:
    """
    Pick a model when switching provider.

    `custom` has an empty preset list (its models are discovered from
    /models at runtime), so indexing it raises IndexError. Fall back to the
    model already in use rather than clobbering it.
    """
    models = list_models_raw(name)
    return models[0] if models else current
