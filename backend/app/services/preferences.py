"""User preferences (non-secret) stored in SQLite."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from ..db import session_scope
from ..models import Preference


class Preferences(BaseModel):
    use_gemini: bool = True
    whisper_model: Literal["tiny", "base", "small", "medium"] = "base"
    default_min_duration: float = Field(15, ge=3, le=600)
    default_max_duration: float = Field(60, ge=5, le=600)
    max_candidates: int = Field(20, ge=3, le=30)
    caption_style: Literal["bold_viral", "clean", "minimal"] = "bold_viral"
    caption_overrides: dict[str, Any] = Field(default_factory=dict)
    captions: bool = True
    smart_reframe: bool = True
    split_screen: bool = False
    silence_removal: bool = True
    aggressive_silence: bool = False
    auto_zoom: bool = True
    broll_mode: Literal["off", "local"] = "off"
    broll_allow_unstated: bool = False
    music_file: str | None = None
    music_volume: float = Field(0.15, ge=0.0, le=1.0)
    denoise: bool = True
    normalize_audio: bool = True
    output_resolution: Literal["1080x1920", "720x1280"] = "1080x1920"
    output_fps: Literal[30, 60] = 30
    crf: int = Field(18, ge=14, le=30)
    encoder_preset: Literal["ultrafast", "superfast", "veryfast", "faster", "fast", "medium"] = "veryfast"
    variants: list[Literal["A", "B", "C"]] = ["A", "B", "C"]  # pydantic copies mutable defaults
    hook_seconds: float = Field(3.5, ge=0, le=15)
    # sound design (effects from the user's own SFX library)
    sound_design: Literal["off", "subtle", "balanced", "punchy"] = "balanced"
    sfx_volume: float = Field(1.0, ge=0.0, le=2.0)
    sfx_playful: bool = False  # comedic / crowd sounds, only on clips the AI reads as humorous
    sfx_library_path: str = ""
    # colour grade (preset name from services/color_grade.PRESETS + fine-tuning shared by shorts and long-form)
    color_grade: str = "none"
    grade_overrides: dict[str, Any] = Field(default_factory=dict)
    long_form_color_grade: str = "none"
    # long-form (16:9) clips
    long_form_min_duration: float = Field(300, ge=60, le=3600)
    long_form_max_duration: float = Field(1200, ge=120, le=5400)
    long_form_max_clips: int = Field(10, ge=1, le=20)
    long_form_resolution: Literal["1920x1080", "1280x720"] = "1920x1080"
    long_form_captions: bool = False
    long_form_cold_open: bool = True
    long_form_silence_removal: bool = True
    long_form_sound_design: Literal["off", "subtle", "balanced", "punchy"] = "balanced"

    @field_validator("color_grade", "long_form_color_grade")
    @classmethod
    def _grade(cls, v: str) -> str:
        from .color_grade import PRESETS

        return v if v in PRESETS else "none"

    @field_validator("grade_overrides")
    @classmethod
    def _grade_overrides(cls, v: dict[str, Any]) -> dict[str, Any]:
        from .color_grade import EDITABLE_FIELDS

        return {k: val for k, val in (v or {}).items() if k in EDITABLE_FIELDS}

    @field_validator("sfx_library_path")
    @classmethod
    def _sfx_dir(cls, v: str) -> str:
        from pathlib import Path

        v = (v or "").strip()
        # existence is checked when the user saves it (update_preferences); a folder that disappears
        # later must not invalidate every other preference on load
        return str(Path(v).expanduser()) if v else ""

    @model_validator(mode="after")
    def _music(self) -> Preferences:
        """Background music must be a file in workspace/music/ or in the sound library folder."""
        v = self.music_file
        if not v:
            self.music_file = None
            return self
        from pathlib import Path

        from ..paths import is_within, music_dir

        p = Path(v)
        if p.is_absolute() and self.sfx_library_path and p.is_file() and is_within(p, Path(self.sfx_library_path)):
            return self
        p = music_dir() / p.name
        self.music_file = str(p) if p.is_file() and is_within(p, music_dir()) else None
        return self


KEY = "preferences"


def get_preferences() -> Preferences:
    with session_scope() as s:
        row = s.get(Preference, KEY)
        data = row.value if row else {}
    try:
        return Preferences.model_validate(data or {})
    except Exception:  # noqa: BLE001 - corrupt prefs should never block the app
        return Preferences()


def update_preferences(changes: dict[str, Any]) -> Preferences:
    lib = str(changes.get("sfx_library_path") or "").strip()
    if lib:
        from pathlib import Path

        if not Path(lib).expanduser().is_dir():
            raise ValueError(f"Sound library folder not found: {lib}")
    current = get_preferences().model_dump()
    current.update({k: v for k, v in changes.items() if k in Preferences.model_fields})
    prefs = Preferences.model_validate(current)
    if prefs.default_min_duration > prefs.default_max_duration:
        raise ValueError("Default minimum duration must not exceed the maximum.")
    if prefs.long_form_min_duration > prefs.long_form_max_duration:
        raise ValueError("Long-form minimum length must not exceed the maximum.")
    with session_scope() as s:
        s.merge(Preference(key=KEY, value=prefs.model_dump()))
    return prefs
