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


class WritingMode(StrEnum):
    """How chapter documents are written and stored."""

    prose = "prose"
    screenplay = "screenplay"


MODE_LABELS: dict[WritingMode, str] = {
    WritingMode.prose: "Prose",
    WritingMode.screenplay: "Screenplay (Fountain)",
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
    # Per-chapter length override. When unset, the story default and then the
    # global config value apply.
    words: int | None = None


class IdeaReview(BaseModel):
    """Editor feedback on the author's idea.

    The review is advisory only. ``suggestions`` is parsed so the author can
    explicitly accept individual items; nothing is applied automatically.
    """

    text: str = ""
    suggestions: list[str] = Field(default_factory=list)
    reviewed_at: datetime | None = None


class StoryMeta(BaseModel):
    title: str
    author: str = ""
    genre: str = ""
    tone: str = ""
    # Prose stories keep Markdown chapters; screenplay stories keep Fountain
    # chapters. The mode also selects the writer prompt and the export default.
    mode: WritingMode = WritingMode.prose
    logline: str = ""
    pov: POV = POV.third_limited
    tense: str = "past"
    target_words: int | None = None
    # Story-level chapter length. Individual outline entries can override it.
    words_per_chapter: int | None = None
    # When true, each chapter prompt includes the previous chapter's ending and
    # asks the model to carry time, place, characters, and threads forward.
    continuity: bool = True
    # Preferences gathered by the interactive session. They are part of the
    # story bible so every later generation call sees them.
    opening: str = ""
    ending: str = ""
    references: list[str] = Field(default_factory=list)
    notes: str = ""
    target_chapters: int | None = None
    export_format: str = "md"
    # Editor feedback is stored, but only author-approved suggestions enter the
    # story bible.
    idea_review: IdeaReview = Field(default_factory=IdeaReview)
    accepted_suggestions: list[str] = Field(default_factory=list)
    characters: list[Character] = Field(default_factory=list)
    outline: list[OutlineEntry] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
