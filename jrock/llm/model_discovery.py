"""Auto-discover and cache available models from the configured provider."""
from __future__ import annotations

import json
import asyncio
from pathlib import Path
from typing import Optional

import httpx

from ..config import Settings

CACHE_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "discovered_models.json"


async def discover_models(settings: Settings, timeout: int = 30) -> list[str]:
    """
    Query the provider's /models endpoint and return working models.
    
    Caches the result to avoid repeated calls on each startup.
    """
    # Try cache first
    if CACHE_FILE.exists():
        try:
            data = json.loads(CACHE_FILE.read_text())
            if data.get("provider") == settings.provider:
                return data.get("models", [])
        except Exception:
            pass
    
    try:
        # Build endpoint
        base_url = settings.api_keys.get("local_base_url")
        if not base_url:
            if settings.provider == "custom":
                base_url = settings.api_keys.get("AI_BASE_URL", "http://127.0.0.1:20128/v1")
            else:
                from .providers import provider_meta
                base_url = provider_meta(settings.provider).get("base_url", "")
        
        if not base_url:
            return []
        
        # Get API key
        api_key = settings.api_keys.get(settings.provider, "")
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        headers["Content-Type"] = "application/json"
        
        # Query /models
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(f"{base_url.rstrip('/')}/models", headers=headers)
            if r.status_code == 200:
                data = r.json()
                models = [m.get("id") for m in data.get("data", []) if m.get("id")]
                if models:
                    # Cache it
                    CACHE_FILE.write_text(json.dumps({
                        "provider": settings.provider,
                        "models": sorted(models)
                    }))
                    return sorted(models)
    except Exception:
        pass
    
    return []


def get_cached_models(provider: str) -> list[str]:
    """Get models from cache if available."""
    try:
        if CACHE_FILE.exists():
            data = json.loads(CACHE_FILE.read_text())
            if data.get("provider") == provider:
                return data.get("models", [])
    except Exception:
        pass
    return []
