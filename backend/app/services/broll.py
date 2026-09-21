"""Optional local B-roll. Only user-supplied, licensed files in workspace/broll/ are ever used.

Metadata (optional): workspace/broll/broll.json
  [{"file": "nyc_night.mp4", "tags": ["new york", "skyline", "night"], "license": "own footage"}]
Without metadata, words in the filename are used as tags.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..candidates.dedupe import content_tokens
from ..paths import broll_dir
from ..schemas.ai import CampaignRules

log = logging.getLogger(__name__)
VIDEO_EXT = {".mp4", ".mov", ".webm", ".m4v"}


@dataclass
class BrollItem:
    path: str
    tags: set[str]
    license: str = ""


def index_broll(directory: Path | None = None) -> list[BrollItem]:
    d = directory or broll_dir()
    meta: dict[str, dict[str, Any]] = {}
    meta_file = d / "broll.json"
    if meta_file.exists():
        try:
            for entry in json.loads(meta_file.read_text(encoding="utf-8")):
                if isinstance(entry, dict) and entry.get("file"):
                    meta[Path(str(entry["file"])).name] = entry
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Ignoring invalid broll.json: %s", e)
    items = []
    for p in sorted(d.iterdir()) if d.exists() else []:
        if p.suffix.lower() not in VIDEO_EXT or not p.is_file():
            continue
        m = meta.get(p.name, {})
        tags = content_tokens(" ".join(m.get("tags", [])) + " " + re.sub(r"[_\-.]+", " ", p.stem))
        items.append(BrollItem(path=str(p), tags=tags, license=str(m.get("license", ""))))
    return items


def search_broll(query: str, items: list[BrollItem], min_score: float = 0.2) -> BrollItem | None:
    q = content_tokens(query)
    best, best_s = None, 0.0
    for it in items:
        if not q or not it.tags:
            continue
        s = len(q & it.tags) / len(q)
        if s > best_s:
            best, best_s = it, s
    return best if best_s >= min_score else None


def broll_permitted(rules: CampaignRules | None, mode: str, allow_when_unstated: bool = False) -> tuple[bool, str]:
    if mode != "local":
        return False, "B-roll is off."
    if rules is not None and rules.broll_allowed is False:
        return False, "Campaign prohibits B-roll."
    if rules is None or rules.broll_allowed is None:
        if not allow_when_unstated:
            return False, "Campaign rules don't state that B-roll is allowed (enable 'allow when unstated' to override)."
    return True, "B-roll permitted."


def plan_broll(points: list[dict[str, Any]], duration: float, items: list[BrollItem], max_events: int = 2,
               max_len: float = 2.5, min_spacing: float = 6.0) -> list[dict[str, Any]]:
    """points use OUTPUT timeline seconds: {timestamp, duration, query}."""
    events: list[dict[str, Any]] = []
    for p in sorted(points, key=lambda x: x["timestamp"]):
        if len(events) >= max_events:
            break
        t = float(p["timestamp"])
        if t < 2.5 or t > duration - 1.5:  # keep the hook and ending on the speaker
            continue
        if any(abs(t - e["start"]) < min_spacing for e in events):
            continue
        hit = search_broll(p.get("query", ""), items)
        if not hit:
            continue
        d = min(max_len, float(p.get("duration", 2.0)), duration - 1.0 - t)
        if d < 0.8:
            continue
        events.append({"start": round(t, 3), "end": round(t + d, 3), "path": hit.path, "query": p.get("query", ""),
                       "license": hit.license})
    return events
