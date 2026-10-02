from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # pragma: no cover
    pass

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

for _d in ("sessions", "memory", "souls", "skills", "mcp", "workspace"):
    (DATA_DIR / _d).mkdir(parents=True, exist_ok=True)

CONFIG_FILE = DATA_DIR / "config.json"


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _env_ids(key: str) -> list[int]:
    out = []
    for part in _env(key, "").split(","):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return out


@dataclass
class Settings:
    # telegram
    tg_token: str = ""
    tg_allowed_ids: list[int] = field(default_factory=list)
    owner_id: int = 0
    # llm
    provider: str = "openai"
    model: str = "gpt-4o"
    thinking: str = "medium"  # off|low|medium|high
    auto_compat: bool = True
    api_keys: dict[str, str] = field(default_factory=dict)
    # agent
    auto_approve_on_edit: bool = False
    learning: bool = True
    default_soul: str = "fable-5.1"
    terminal_allowed: bool = True
    max_agent_steps: int = 30
    workspace: str = ""   # where terminal/fs tools operate (default: project root)
    # generator
    fal_key: str = ""
    edge_tts: bool = True

    def save(self) -> None:
        CONFIG_FILE.write_text(json.dumps(asdict(self), indent=2))

    @classmethod
    def load(cls) -> "Settings":
        s = cls()
        if CONFIG_FILE.exists():
            try:
                data = json.loads(CONFIG_FILE.read_text())
                for k, v in data.items():
                    if hasattr(s, k):
                        setattr(s, k, v)
            except Exception:
                pass
        # Accept both TG_BOT_TOKEN and TELEGRAM_BOT_TOKEN
        s.tg_token = _env("TG_BOT_TOKEN", _env("TELEGRAM_BOT_TOKEN", s.tg_token))
        # Accept both TG_ALLOWED_IDS and TELEGRAM_ADMIN_IDS
        ids = _env_ids("TG_ALLOWED_IDS") or _env_ids("TELEGRAM_ADMIN_IDS")
        if ids:
            s.tg_allowed_ids = sorted(set(ids + s.tg_allowed_ids))
            s.owner_id = ids[0]
        elif s.tg_allowed_ids:
            s.owner_id = s.owner_id or s.tg_allowed_ids[0]
        s.provider = _env("PROVIDER", s.provider)
        s.model = _env("MODEL", _env("AI_MODEL", s.model))
        if _env("API_KEY"):
            s.api_keys[s.provider] = _env("API_KEY")
        # AI_API_KEY / AI_BASE_URL → maps to 'custom' provider
        if _env("AI_API_KEY"):
            s.api_keys["custom"] = _env("AI_API_KEY")
            if not _env("PROVIDER"):           # only override if not explicitly set
                s.provider = "custom"
        if _env("AI_BASE_URL"):
            s.api_keys["local_base_url"] = _env("AI_BASE_URL")
        for p in ("openai", "deepseek", "groq", "openrouter", "google", "anthropic", "local"):
            k = _env(f"{p.upper()}_API_KEY")
            if k:
                s.api_keys[p] = k
        if _env("LOCAL_BASE_URL"):
            s.api_keys.setdefault("local_base_url", _env("LOCAL_BASE_URL"))
        s.fal_key = _env("FAL_KEY", s.fal_key)
        return s

    def allowed(self, user_id: int) -> bool:
        return not self.tg_allowed_ids or user_id in self.tg_allowed_ids

    def is_owner(self, user_id: int) -> bool:
        first = self.tg_allowed_ids[0] if self.tg_allowed_ids else 0
        return user_id in (self.owner_id, first)
