"""Versioned prompt templates loaded from backend/prompts/*.txt.

File format: first line ``VERSION: <id>``; the rest is the template. Placeholders use
``{{name}}`` so JSON braces in prompts need no escaping.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ..config import PROMPTS_DIR

_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


class PromptError(RuntimeError):
    pass


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    template: str

    @property
    def placeholders(self) -> set[str]:
        return set(_PLACEHOLDER.findall(self.template))

    def render(self, **values: object) -> str:
        missing = self.placeholders - values.keys()
        if missing:
            raise PromptError(f"prompt {self.name} missing values: {sorted(missing)}")
        return _PLACEHOLDER.sub(lambda m: str(values[m.group(1)]), self.template)


def parse_prompt(name: str, raw: str) -> Prompt:
    first, _, rest = raw.partition("\n")
    m = re.match(r"^VERSION:\s*(\S+)\s*$", first.strip())
    if not m:
        raise PromptError(f"prompt {name} must start with 'VERSION: <id>'")
    return Prompt(name=name, version=f"{name}@{m.group(1)}", template=rest.strip() + "\n")


@lru_cache
def load_prompt(name: str, directory: Path = PROMPTS_DIR) -> Prompt:
    if not re.fullmatch(r"[a-z_]+", name):
        raise PromptError("invalid prompt name")
    path = directory / f"{name}.txt"
    if not path.exists():
        raise PromptError(f"prompt file not found: {path}")
    return parse_prompt(name, path.read_text(encoding="utf-8"))
