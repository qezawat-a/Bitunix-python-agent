from __future__ import annotations

from ..config import Settings

DENIED = ("This bot is private. Ask the owner to add your Telegram ID "
          "to TG_ALLOWED_IDS.")


def allowed(settings: Settings, user_id: int) -> bool:
    return settings.allowed(user_id)


def is_owner(settings: Settings, user_id: int) -> bool:
    return settings.is_owner(user_id)
