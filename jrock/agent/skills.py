"""Skills = reusable instruction folders (data/skills/<name>/SKILL.md)."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

DIR = Path(__file__).resolve().parents[2] / "data" / "skills"
DIR.mkdir(parents=True, exist_ok=True)


class SkillStore:
    def names(self) -> list[str]:
        return sorted(p.name for p in DIR.iterdir() if p.is_dir() and (p / "SKILL.md").exists())

    def index(self) -> str:
        lines = []
        for name in self.names():
            desc = self.meta(name).get("description", "")
            lines.append(f"- {name}: {desc}")
        return "\n".join(lines) or "(no skills installed)"

    def _file(self, name: str) -> Path:
        return DIR / name / "SKILL.md"

    def meta(self, name: str) -> dict:
        f = self._file(name)
        if not f.exists():
            return {}
        head = f.read_text()[:800]
        out = {}
        m = re.match(r"^---\n(.*?)\n---", head, re.S)
        if m:
            for line in m.group(1).splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    out[k.strip()] = v.strip()
        return out

    def read(self, name: str) -> str:
        f = self._file(name)
        return f.read_text() if f.exists() else ""

    def add(self, source: str) -> str:
        """Add a skill from a local folder (with SKILL.md) or a .md file."""
        src = Path(source).expanduser().resolve()
        if src.is_dir() and (src / "SKILL.md").exists():
            dst = DIR / src.name
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
            return f"Skill '{src.name}' installed."
        if src.is_file() and src.suffix.lower() in (".md", ".markdown"):
            dst = DIR / src.stem
            dst.mkdir(parents=True, exist_ok=True)
            shutil.copy(src, dst / "SKILL.md")
            return f"Skill '{src.stem}' installed."
        return "Give me a folder containing SKILL.md, or a .md file."

    def remove(self, name: str) -> bool:
        d = DIR / name
        if d.exists():
            shutil.rmtree(d)
            return True
        return False
