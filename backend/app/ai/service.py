"""Provider selection, response caching and usage accounting."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

from pydantic import BaseModel
from sqlalchemy import update

from ..config import get_settings
from ..db import session_scope
from ..models import AIResponseCache, GeminiFileRef, Project
from ..utils.hashing import cache_key
from .demo import DemoProvider
from .gemini import FileRefStore, GeminiProvider
from .provider import AIProvider, VideoRef

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class ProjectUsage:
    def __init__(self, project_id: str):
        self.project_id = project_id

    def _bump(self, **values: int) -> None:
        with session_scope() as s:
            s.execute(update(Project).where(Project.id == self.project_id).values(
                **{k: getattr(Project, k) + v for k, v in values.items()}))

    def request(self, input_tokens: int = 0, output_tokens: int = 0) -> None:
        self._bump(gemini_requests=1, input_tokens=input_tokens, output_tokens=output_tokens)

    def upload(self) -> None:
        self._bump(gemini_uploads=1)

    def cached(self) -> None:
        self._bump(cached_requests=1)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def db_file_store(project_id: str) -> FileRefStore:
    usage = ProjectUsage(project_id)

    def get(sha: str) -> VideoRef | None:
        with session_scope() as s:
            row = s.get(GeminiFileRef, sha)
            if not row:
                return None
            exp = _aware(row.expires_at)
            if exp and exp - datetime.now(UTC) < timedelta(hours=1):
                return None
            return VideoRef(uri=row.file_uri, mime_type=row.mime_type, name=row.file_name)

    def put(sha: str, ref: VideoRef, expires: datetime | None) -> None:
        with session_scope() as s:
            row = s.get(GeminiFileRef, sha) or GeminiFileRef(source_sha256=sha, file_name="", file_uri="", mime_type="")
            row.file_name, row.file_uri, row.mime_type = ref.name, ref.uri, ref.mime_type
            row.expires_at = _aware(expires)
            row.created_at = datetime.now(UTC)
            s.merge(row)

    return FileRefStore(get=get, put=put, on_cached=usage.cached)


def gemini_status() -> dict[str, Any]:
    s = get_settings()
    return {"configured": s.gemini_configured, "model": s.model_name}


def get_provider(project_id: str, use_gemini: bool) -> AIProvider:
    s = get_settings()
    if use_gemini and s.gemini_configured:
        return GeminiProvider(api_key=s.gemini_api_key.strip(), model=s.model_name, timeout_s=s.gemini_timeout_s,
                              usage=ProjectUsage(project_id), file_store=db_file_store(project_id))
    return DemoProvider()


def cached_call(project_id: str, provider: AIProvider, operation: str, key_parts: dict[str, Any],
                fn: Callable[[], T], model_cls: type[T]) -> tuple[T, bool]:
    """Return a cached AI result for identical inputs, otherwise call and store it.

    The key includes provider, model, prompt version and caller-supplied parts (source hash,
    campaign-rule hash, parameters). Demo results are not cached (they are free and instant).
    """
    if provider.is_demo:
        return fn(), False
    key = cache_key(op=operation, provider=provider.name, model=provider.model,
                    prompt=provider.prompt_version(operation), **key_parts)
    with session_scope() as s:
        row = s.get(AIResponseCache, key)
        if row is not None:
            try:
                obj = model_cls.model_validate(row.response)
                ProjectUsage(project_id).cached()
                log.info("AI cache hit: %s (%s)", operation, key[:12])
                return obj, True
            except Exception:  # noqa: BLE001 - schema evolved; recompute
                s.delete(row)
    obj = fn()
    with session_scope() as s:
        s.merge(AIResponseCache(cache_key=key, operation=operation, provider=provider.name, model=provider.model,
                                prompt_version=provider.prompt_version(operation), response=obj.model_dump(mode="json")))
    return obj, False
