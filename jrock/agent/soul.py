"""Souls = persona/system-prompt documents. Default soul: fable-5.1."""
from __future__ import annotations

import re
from pathlib import Path

DIR = Path(__file__).resolve().parents[2] / "data" / "souls"
DIR.mkdir(parents=True, exist_ok=True)


class SoulStore:
    def __init__(self) -> None:
        for f in DIR.glob("*.md"):
            pass

    def names(self) -> list[str]:
        return sorted(f.stem for f in DIR.glob("*.md"))

    def exists(self, name: str) -> bool:
        return (DIR / f"{name}.md").exists()

    def get(self, name: str) -> str:
        p = DIR / f"{name}.md"
        return p.read_text() if p.exists() else ""

    def save(self, name: str, text: str) -> Path:
        name = re.sub(r"[^a-zA-Z0-9._-]", "", name.strip()) or "custom"
        p = DIR / f"{name}.md"
        p.write_text(text.strip() + "\n")
        return p

    def delete(self, name: str) -> bool:
        p = DIR / f"{name}.md"
        if p.exists():
            p.unlink()
            return True
        return False
