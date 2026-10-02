"""Central configuration loaded from .env — no hardcoded values."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = Path(__file__).parent


def _list(val: str | None) -> List[int]:
    """Parse comma-separated integers."""
    if not val:
        return []
    return [int(x.strip()) for x in val.split(",") if x.strip()]


class Config:
    # ── Telegram ─────────────────────────────────────────
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    ADMIN_IDS: List[int] = _list(os.getenv("TELEGRAM_ADMIN_IDS", ""))

    # ── AI Provider ──────────────────────────────────────
    AI_BASE_URL: str = os.getenv("AI_BASE_URL", "https://api.openai.com")
    AI_API_KEY: str = os.getenv("AI_API_KEY", "")
    AI_MODEL: str = os.getenv("AI_MODEL", "")          # empty = auto-discover
    AI_MAX_TOKENS: int = int(os.getenv("AI_MAX_TOKENS", "65536"))
    AI_TEMPERATURE: float = float(os.getenv("AI_TEMPERATURE", "1.0"))

    # ── Bitunix ───────────────────────────────────────────
    BITUNIX_API_KEY: str = os.getenv("BITUNIX_API_KEY", "")
    BITUNIX_SECRET_KEY: str = os.getenv("BITUNIX_SECRET_KEY", "")
    BITUNIX_BASE_URL: str = os.getenv("BITUNIX_BASE_URL", "https://fapi.bitunix.com")
    BITUNIX_WS_PUBLIC: str = os.getenv("BITUNIX_WS_PUBLIC", "wss://fapi.bitunix.com/public/")
    BITUNIX_WS_PRIVATE: str = os.getenv("BITUNIX_WS_PRIVATE", "wss://fapi.bitunix.com/private/")

    # ── Trading ───────────────────────────────────────────
    TRADING_ENABLED: bool = os.getenv("TRADING_ENABLED", "false").lower() == "true"
    DEFAULT_SYMBOL: str = os.getenv("DEFAULT_SYMBOL", "BTCUSDT")
    DEFAULT_MARGIN_COIN: str = os.getenv("DEFAULT_MARGIN_COIN", "USDT")
    DEFAULT_LEVERAGE: int = int(os.getenv("DEFAULT_LEVERAGE", "10"))
    DEFAULT_MARGIN_MODE: str = os.getenv("DEFAULT_MARGIN_MODE", "ISOLATION")
    DEFAULT_POSITION_MODE: str = os.getenv("DEFAULT_POSITION_MODE", "ONE_WAY")
    RISK_PERCENT: float = float(os.getenv("RISK_PERCENT", "1.0"))
    MAX_OPEN_POSITIONS: int = int(os.getenv("MAX_OPEN_POSITIONS", "3"))
    PAPER_TRADING: bool = os.getenv("PAPER_TRADING", "true").lower() == "true"

    # ── Database ──────────────────────────────────────────
    # Neon (Postgres) is the only database: agent memory, settings and trades.
    # Required — the bot refuses to start without it.
    NEON_DATABASE_URL: str = os.getenv("NEON_DATABASE_URL", "")

    # ── Logging ───────────────────────────────────────────
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_FILE: str = os.getenv("LOG_FILE", "./logs/agent.log")

    # ── Generator ─────────────────────────────────────────
    IMAGE_PROVIDER: str = os.getenv("IMAGE_PROVIDER", "openai")
    TTS_PROVIDER: str = os.getenv("TTS_PROVIDER", "openai")

    # ── Runtime mutable state (updated by /set commands) ──
    _active_model: Optional[str] = None
    _active_soul: Optional[str] = None
    _learning_enabled: bool = True
    _auto_approve: bool = False

    @classmethod
    def get_model(cls) -> str:
        return cls._active_model or cls.AI_MODEL

    @classmethod
    def set_model(cls, model: str) -> None:
        cls._active_model = model
        os.environ["AI_MODEL"] = model

    @classmethod
    def is_admin(cls, user_id: int) -> bool:
        return not cls.ADMIN_IDS or user_id in cls.ADMIN_IDS

    @classmethod
    def reload(cls) -> None:
        """Reload from .env (called after /set_api_key etc.)."""
        load_dotenv(override=True)
        cls.AI_API_KEY = os.getenv("AI_API_KEY", "")
        cls.AI_BASE_URL = os.getenv("AI_BASE_URL", cls.AI_BASE_URL)
        cls.BITUNIX_API_KEY = os.getenv("BITUNIX_API_KEY", "")
        cls.BITUNIX_SECRET_KEY = os.getenv("BITUNIX_SECRET_KEY", "")
        cls.DEFAULT_SYMBOL = os.getenv("DEFAULT_SYMBOL", cls.DEFAULT_SYMBOL)
        cls.DEFAULT_LEVERAGE = int(os.getenv("DEFAULT_LEVERAGE", str(cls.DEFAULT_LEVERAGE)))
        cls.DEFAULT_MARGIN_MODE = os.getenv("DEFAULT_MARGIN_MODE", cls.DEFAULT_MARGIN_MODE)
        cls.DEFAULT_POSITION_MODE = os.getenv("DEFAULT_POSITION_MODE", cls.DEFAULT_POSITION_MODE)
        cls.RISK_PERCENT = float(os.getenv("RISK_PERCENT", str(cls.RISK_PERCENT)))
        cls.MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", str(cls.MAX_OPEN_POSITIONS)))
        cls.PAPER_TRADING = os.getenv("PAPER_TRADING", str(cls.PAPER_TRADING)).lower() == "true"
        cls.TRADING_ENABLED = os.getenv("TRADING_ENABLED", str(cls.TRADING_ENABLED)).lower() == "true"
        # Clear lru_cache so get_config() returns fresh values
        get_config.cache_clear()


@lru_cache(maxsize=1)
def get_config() -> Config:
    return Config()
