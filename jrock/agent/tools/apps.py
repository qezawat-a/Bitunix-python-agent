from __future__ import annotations

import json
from pathlib import Path

import httpx

from ..context import AgentContext

APPS_FILE = Path(__file__).resolve().parents[3] / "data" / "workspace" / "apps.json"


def load_apps() -> dict:
    if APPS_FILE.exists():
        try:
            return json.loads(APPS_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_apps(apps: dict) -> None:
    APPS_FILE.parent.mkdir(parents=True, exist_ok=True)
    APPS_FILE.write_text(json.dumps(apps, indent=2))


async def call(args: dict, ctx: AgentContext) -> str:
    apps = load_apps()
    name = args.get("app", "")
    app = apps.get(name)
    if not app:
        return f"App '{name}' not connected. Add: /app_connector {name} <url> [key]"
    action = args.get("action", "GET")
    try:
        payload = json.loads(args.get("payload", "{}"))
    except json.JSONDecodeError:
        payload = args.get("payload", "")
    if action.startswith("http"):
        url = action
    else:
        url = app.get("base_url", "").rstrip("/") + "/" + action.lstrip("/")
    headers = {"Content-Type": "application/json"}
    if app.get("api_key"):
        headers[app.get("header", "Authorization")] = app.get("prefix", "Bearer ") + app["api_key"]
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            if action.upper() in ("GET", "DELETE"):
                r = await c.request(action.upper(), url, headers=headers)
            else:
                r = await c.request(action.upper(), url, headers=headers, json=payload)
        return f"HTTP {r.status_code}\n{r.text[:4000]}"
    except Exception as e:
        return f"App call failed: {e}"
