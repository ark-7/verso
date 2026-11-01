"""Generation orchestration: prompts + provider + durable writes."""

from __future__ import annotations

import os
from collections.abc import Callable

from .models import OutlineEntry
from .prompts import (
    EDITOR_SYSTEM,
    WRITER_SYSTEM,
    chapter_prompt,
    outline_prompt,
    parse_outline,
    story_bible,
)
from .providers.base import GenerationRequest, Provider
from .store import Chapter, StoryProject


class GenerationError(Exception):
    """A user-facing generation problem (bad chapter number, empty output...)."""


def _system(meta, base: str, *, include_outline: bool = True) -> str:
    return base + "\n\n" + story_bible(meta, include_outline=include_outline)


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
    words = words or 1500
    path = project.chapter_path_for(number, entry.title)
    if path.exists() and not force:
        raise GenerationError(
            f"{path.name} already exists. Pass --force to overwrite it."
        )

    previous_tail = ""
    if number > 1:
        try:
            previous_tail = _tail(project.chapter(number - 1).body, 1600)
        except Exception as exc:  # convert to a clear message
            raise GenerationError(
                f"Cannot write chapter {number} before chapter {number - 1} exists."
            ) from exc

    request = GenerationRequest(
        prompt=chapter_prompt(
            meta, number, entry, previous_tail=previous_tail, words=words
        ),
        system=_system(meta, WRITER_SYSTEM),
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


def _tail(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[-limit:]
    # Start at a word boundary so the excerpt doesn't begin mid-word.
    space = cut.find(" ")
    return cut[space + 1 :] if 0 < space < 40 else cut
