"""Data model for a story project.

The on-disk source of truth is ``story.json`` (metadata) plus one Markdown
file per chapter. Nothing in this module knows about LLM providers or exports.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(UTC)


class POV(StrEnum):
    first = "first"
    third_limited = "third-limited"
    third_omniscient = "third-omniscient"


POV_LABELS: dict[POV, str] = {
    POV.first: "First person",
    POV.third_limited: "Third person limited",
    POV.third_omniscient: "Third person omniscient",
}


class Character(BaseModel):
    name: str
    role: str = ""
    description: str = ""


class OutlineEntry(BaseModel):
    """One planned chapter. The title becomes the chapter heading."""

    title: str
    summary: str = ""
    # User-supplied guidance for this chapter. It takes precedence over the
    # generated beat when the chapter is written.
    direction: str = ""


class StoryMeta(BaseModel):
    title: str
    author: str = ""
    genre: str = ""
    tone: str = ""
    logline: str = ""
    pov: POV = POV.third_limited
    tense: str = "past"
    target_words: int | None = None
    # Preferences gathered by the interactive session. They are part of the
    # story bible so every later generation call sees them.
    opening: str = ""
    ending: str = ""
    references: list[str] = Field(default_factory=list)
    notes: str = ""
    target_chapters: int | None = None
    export_format: str = "md"
    characters: list[Character] = Field(default_factory=list)
    outline: list[OutlineEntry] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
