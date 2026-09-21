"""Database tables.

API keys are never stored here.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str = "") -> str:
    return prefix + secrets.token_hex(6)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("p"))
    name: Mapped[str] = mapped_column(String(200))
    campaign_name: Mapped[str] = mapped_column(String(200), default="")
    campaign_rules_text: Mapped[str] = mapped_column(Text, default="")
    campaign_rules_hash: Mapped[str] = mapped_column(String(64), default="")
    campaign_rules_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    campaign_rules_source: Mapped[str] = mapped_column(String(20), default="")  # gemini|heuristic
    platforms: Mapped[list[str]] = mapped_column(JSON, default=list)
    desired_clip_count: Mapped[int] = mapped_column(Integer, default=10)
    duration_mode: Mapped[str] = mapped_column(String(10), default="auto")  # auto|manual
    min_duration: Mapped[float] = mapped_column(Float, default=15.0)
    max_duration: Mapped[float] = mapped_column(Float, default=60.0)
    use_gemini: Mapped[bool] = mapped_column(Boolean, default=True)
    long_form_mode: Mapped[str] = mapped_column(String(10), default="auto")  # auto|manual|off
    long_form_count: Mapped[int] = mapped_column(Integer, default=0)  # used when manual
    status: Mapped[str] = mapped_column(String(30), default="created")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)  # cleared from the dashboard history, files kept
    # API usage counters
    gemini_requests: Mapped[int] = mapped_column(Integer, default=0)
    gemini_uploads: Mapped[int] = mapped_column(Integer, default=0)
    cached_requests: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    analysis_provider: Mapped[str] = mapped_column(String(20), default="")  # gemini|demo
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    source: Mapped[SourceVideo | None] = relationship(back_populates="project", uselist=False, cascade="all, delete-orphan")
    candidates: Mapped[list[Candidate]] = relationship(back_populates="project", cascade="all, delete-orphan")


class SourceVideo(Base):
    __tablename__ = "source_videos"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("s"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), unique=True)
    origin: Mapped[str] = mapped_column(String(20))  # upload|youtube
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    permission_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    original_filename: Mapped[str] = mapped_column(String(300), default="")
    stored_path: Mapped[str] = mapped_column(Text, default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="", index=True)
    probe: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    proxy_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    audio_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    project: Mapped[Project] = relationship(back_populates="source")


class TranscriptRecord(Base):
    """Cache of transcripts keyed by source hash + whisper model."""

    __tablename__ = "transcripts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_sha256: Mapped[str] = mapped_column(String(64), index=True)
    whisper_model: Mapped[str] = mapped_column(String(40))
    language: Mapped[str | None] = mapped_column(String(20), nullable=True)
    json_path: Mapped[str] = mapped_column(Text)
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GeminiFileRef(Base):
    """Cached Gemini Files API reference so a source isn't uploaded twice (files live ~48h)."""

    __tablename__ = "gemini_files"

    source_sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    file_name: Mapped[str] = mapped_column(String(200))
    file_uri: Mapped[str] = mapped_column(Text)
    mime_type: Mapped[str] = mapped_column(String(60))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AIResponseCache(Base):
    __tablename__ = "ai_cache"

    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    operation: Mapped[str] = mapped_column(String(40))
    provider: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(80))
    prompt_version: Mapped[str] = mapped_column(String(40))
    response: Mapped[dict[str, Any]] = mapped_column(JSON)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Analysis(Base):
    __tablename__ = "analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(80))
    cache_key: Mapped[str] = mapped_column(String(64))
    video_analysis: Mapped[dict[str, Any]] = mapped_column(JSON)
    raw_candidate_count: Mapped[int] = mapped_column(Integer, default=0)
    kept_candidate_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Candidate(Base):
    __tablename__ = "candidates"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("c"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    candidate_id: Mapped[str] = mapped_column(String(40))
    rank: Mapped[int] = mapped_column(Integer, default=0)
    ai_start: Mapped[float] = mapped_column(Float)
    ai_end: Mapped[float] = mapped_column(Float)
    start: Mapped[float] = mapped_column(Float)
    end: Mapped[float] = mapped_column(Float)
    viral_score: Mapped[float] = mapped_column(Float, default=0)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)  # full validated ViralCandidate
    snap_notes: Mapped[list[str]] = mapped_column(JSON, default=list)
    transcript_text: Mapped[str] = mapped_column(Text, default="")
    hooks: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    hook_index: Mapped[int] = mapped_column(Integer, default=0)
    posting_copy: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    compliance_status: Mapped[str] = mapped_column(String(12), default="UNKNOWN")
    compliance: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    selected: Mapped[bool] = mapped_column(Boolean, default=False)
    rejected: Mapped[bool] = mapped_column(Boolean, default=False)
    user_edited: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    project: Mapped[Project] = relationship(back_populates="candidates")
    exports: Mapped[list[ExportRecord]] = relationship(back_populates="candidate", cascade="all, delete-orphan")


class LongFormClip(Base):
    """A minutes-long 16:9 segment with its title/thumbnail package and (after rendering) its export."""

    __tablename__ = "long_form_clips"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("l"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    candidate_id: Mapped[str] = mapped_column(String(40))
    rank: Mapped[int] = mapped_column(Integer, default=0)
    ai_start: Mapped[float] = mapped_column(Float)
    ai_end: Mapped[float] = mapped_column(Float)
    start: Mapped[float] = mapped_column(Float)
    end: Mapped[float] = mapped_column(Float)
    score: Mapped[float] = mapped_column(Float, default=0)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)  # validated LongFormCandidate + chapters/cold open/prompts
    snap_notes: Mapped[list[str]] = mapped_column(JSON, default=list)
    transcript_text: Mapped[str] = mapped_column(Text, default="")
    title_index: Mapped[int] = mapped_column(Integer, default=0)
    selected: Mapped[bool] = mapped_column(Boolean, default=False)
    rejected: Mapped[bool] = mapped_column(Boolean, default=False)
    export: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Job(Base):
    """A long-running pipeline operation (analysis or render batch)."""

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("j"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # analyze|render
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|running|done|error
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    message: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RenderJob(Base):
    __tablename__ = "render_jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("r"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    candidate_pk: Mapped[str] = mapped_column(ForeignKey("candidates.id", ondelete="CASCADE"))
    job_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    options: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ExportRecord(Base):
    __tablename__ = "exports"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("e"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    candidate_pk: Mapped[str] = mapped_column(ForeignKey("candidates.id", ondelete="CASCADE"))
    folder: Mapped[str] = mapped_column(Text)
    files: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # variant -> relative mp4 path
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    compliance_status: Mapped[str] = mapped_column(String(12), default="UNKNOWN")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    candidate: Mapped[Candidate] = relationship(back_populates="exports")


class Preference(Base):
    __tablename__ = "preferences"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)


class PublishJob(Base):
    """One publish attempt of one rendered clip to one platform. Never stores tokens."""

    __tablename__ = "publish_jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("pub"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    candidate_pk: Mapped[str | None] = mapped_column(String(32), nullable=True)
    export_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    platform: Mapped[str] = mapped_column(String(20))  # youtube | tiktok
    mode: Mapped[str] = mapped_column(String(20), default="")  # youtube: upload | tiktok: inbox|direct
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|uploading|done|error
    privacy: Mapped[str] = mapped_column(String(20), default="")
    title: Mapped[str] = mapped_column(Text, default="")
    file_path: Mapped[str] = mapped_column(Text, default="")
    remote_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    remote_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    account_name: Mapped[str] = mapped_column(String(200), default="")
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
