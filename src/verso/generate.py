"""Generation orchestration: prompts + provider + durable writes."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from datetime import UTC, datetime

from .models import IdeaReview, OutlineEntry, WritingMode
from .prompts import (
    EDITOR_SYSTEM,
    REVIEWER_SYSTEM,
    SCREENWRITER_SYSTEM,
    WRITER_SYSTEM,
    chapter_prompt,
    outline_prompt,
    parse_outline,
    parse_suggestions,
    review_prompt,
    story_bible,
)
from .providers.base import GenerationRequest, Provider
from .store import Chapter, StoryProject

DEFAULT_CHAPTER_WORDS = 1500


class GenerationError(Exception):
    """A user-facing generation problem (bad chapter number, empty output...)."""


def chapter_word_target(
    project: StoryProject,
    number: int,
    *,
    explicit: int | None = None,
    fallback: int | None = None,
) -> int:
    """Resolve a chapter's word target.

    Precedence: an explicit request, the chapter's own override, the story
    default, then the caller's fallback (normally ``config.words_per_chapter``).
    """
    if explicit is not None:
        return max(100, int(explicit))
    meta = project.meta
    if 1 <= number <= len(meta.outline):
        entry = meta.outline[number - 1]
        if entry.words:
            return max(100, int(entry.words))
    if meta.words_per_chapter:
        return max(100, int(meta.words_per_chapter))
    return max(100, int(fallback or DEFAULT_CHAPTER_WORDS))


def _system(meta, base: str, *, include_outline: bool = True) -> str:
    return base + "\n\n" + story_bible(meta, include_outline=include_outline)


def _writer_system(meta) -> str:
    return (
        SCREENWRITER_SYSTEM
        if meta.mode == WritingMode.screenplay
        else WRITER_SYSTEM
    )


def generate_outline(
    project: StoryProject,
    provider: Provider,
    *,
    chapters: int = 10,
    premise: str = "",
    max_tokens: int = 2000,
) -> list[OutlineEntry]:
    request = GenerationRequest(
        prompt=outline_prompt(project.meta, chapters, premise),
        system=_system(project.meta, EDITOR_SYSTEM, include_outline=False),
        max_tokens=max_tokens,
    )
    text = provider.complete(request)
    entries = parse_outline(text)
    if not entries:
        raise GenerationError(
            "The model did not return a usable outline. Try again, or pass a "
            "more specific --premise."
        )
    project.meta.outline = entries
    project.save_meta()
    return entries


def write_chapter(
    project: StoryProject,
    provider: Provider,
    number: int | None = None,
    *,
    words: int | None = None,
    force: bool = False,
    continuity: bool | None = None,
    on_token: Callable[[str], None] | None = None,
) -> Chapter:
    meta = project.meta
    if not meta.outline:
        raise GenerationError("No outline yet. Run `verso outline` first.")

    number = number if number is not None else project.next_chapter_number()
    if number < 1 or number > len(meta.outline):
        raise GenerationError(
            f"Chapter {number} is outside the outline (1–{len(meta.outline)})."
        )

    entry = meta.outline[number - 1]
    words = chapter_word_target(project, number, explicit=words)
    continuity = meta.continuity if continuity is None else continuity
    path = project.chapter_path_for(number, entry.title)
    if path.exists() and not force:
        raise GenerationError(
            f"{path.name} already exists. Pass --force to overwrite it."
        )
    if force:
        # A renamed chapter, or a mode switch, would otherwise leave a stale
        # file with the same chapter number behind.
        for chapter in project.chapters():
            if chapter.number == number and chapter.path != path:
                chapter.path.unlink(missing_ok=True)

    previous_tail = ""
    previous_title = ""
    previous_summary = ""
    if number > 1:
        try:
            previous_chapter = project.chapter(number - 1)
        except Exception as exc:  # convert to a clear message
            raise GenerationError(
                f"Cannot write chapter {number} before chapter {number - 1} exists."
            ) from exc
        if continuity:
            previous_tail = _tail(previous_chapter.body, 1600)
            previous_entry = meta.outline[number - 2]
            previous_title = previous_entry.title
            previous_summary = previous_entry.summary

    request = GenerationRequest(
        prompt=chapter_prompt(
            meta,
            number,
            entry,
            previous_tail=previous_tail,
            previous_number=number - 1 if previous_tail else None,
            previous_title=previous_title,
            previous_summary=previous_summary,
            words=words,
        ),
        system=_system(meta, _writer_system(meta)),
        max_tokens=min(16384, int(words * 2.2) + 800),
    )

    heading = project.chapter_heading(number, entry.title)
    tmp = path.with_name(path.name + ".part")
    path.parent.mkdir(parents=True, exist_ok=True)

    parts: list[str] = []
    interrupted = False
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(heading)
            handle.flush()
            parts.append(heading)
            for token in provider.stream(request):
                parts.append(token)
                handle.write(token)
                handle.flush()
                if on_token is not None:
                    on_token(token)
    except KeyboardInterrupt:
        interrupted = True
    except Exception:
        # Keep a partial chapter for recovery, but never an empty shell.
        if not "".join(parts)[len(heading) :].strip():
            tmp.unlink(missing_ok=True)
        raise

    body = "".join(parts)
    if not body[len(heading) :].strip():
        tmp.unlink(missing_ok=True)
        raise GenerationError(
            "Generation was interrupted before any text was written."
            if interrupted
            else "The model returned no text."
        )

    os.replace(tmp, path)
    return project.chapter(number)


def review_idea(
    project: StoryProject,
    provider: Provider,
    *,
    premise: str = "",
    max_tokens: int = 1400,
) -> IdeaReview:
    """Ask the editor to review the author's idea.

    The result is stored as advice. Nothing in the story bible changes until the
    author accepts a specific suggestion.
    """
    request = GenerationRequest(
        prompt=review_prompt(project.meta, premise),
        system=_system(project.meta, REVIEWER_SYSTEM, include_outline=False),
        max_tokens=max_tokens,
    )
    text = provider.complete(request).strip()
    if not text:
        raise GenerationError("The model returned an empty review.")
    review = IdeaReview(
        text=text,
        suggestions=parse_suggestions(text),
        reviewed_at=datetime.now(UTC),
    )
    project.meta.idea_review = review
    project.save_meta()
    return review


def accept_review_suggestions(
    project: StoryProject,
    indexes: Iterable[int],
    *,
    enhancement: str = "",
) -> list[str]:
    """Record author-approved suggestions in the story bible.

    ``indexes`` are 1-based positions in the stored review. ``enhancement``
    lets the author add their own wording on top of a suggestion. Only what the
    author approves here is ever applied; the review itself remains advisory.
    """
    review = project.meta.idea_review
    accepted: list[str] = []
    for index in indexes:
        if not 1 <= index <= len(review.suggestions):
            raise GenerationError(
                f"Suggestion {index} does not exist "
                f"(1–{len(review.suggestions)})."
            )
        text = review.suggestions[index - 1].strip()
        if text and text not in project.meta.accepted_suggestions:
            project.meta.accepted_suggestions.append(text)
            accepted.append(text)

    extra = enhancement.strip()
    if extra and extra not in project.meta.accepted_suggestions:
        project.meta.accepted_suggestions.append(extra)
        accepted.append(extra)

    if accepted:
        project.save_meta()
    return accepted


def clear_accepted_suggestions(project: StoryProject) -> int:
    """Remove every author-approved suggestion from the story bible."""
    count = len(project.meta.accepted_suggestions)
    if count:
        project.meta.accepted_suggestions = []
        project.save_meta()
    return count


def _tail(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[-limit:]
    # Start at a word boundary so the excerpt doesn't begin mid-word.
    space = cut.find(" ")
    return cut[space + 1 :] if 0 < space < 40 else cut
