"""Prompt construction.

Kept in one module so the writing voice is tunable without touching the CLI or
the provider layer. The ``story_bible`` is deliberately stable between calls so
providers that support prompt caching can reuse the prefix.
"""

from __future__ import annotations

import re

from .models import POV_LABELS, OutlineEntry, StoryMeta

WRITER_SYSTEM = """You are a disciplined novelist working as a co-writer. \
Write publishable prose in the voice described by the story bible.

Rules:
- Follow the story bible exactly: point of view, tense, tone, characters, and \
established continuity.
- Show, don't tell. Use concrete sensory detail and natural dialogue.
- Never address the reader, explain your process, or mention being an AI.
- Do not include a chapter heading, title, notes, or commentary. Output prose only.
"""

EDITOR_SYSTEM = """You are a story editor. You design tight, causally linked \
chapter outlines in which each chapter changes the situation and pushes the \
protagonist toward the climax. You write concise, concrete beats, never vague \
promises. Follow the story bible exactly.
"""


def story_bible(meta: StoryMeta, *, include_outline: bool = True) -> str:
    parts = ["# Story bible", f"Title: {meta.title}"]
    if meta.genre:
        parts.append(f"Genre: {meta.genre}")
    if meta.tone:
        parts.append(f"Tone: {meta.tone}")
    parts.append(f"Point of view: {POV_LABELS[meta.pov]}")
    parts.append(f"Tense: {meta.tense}")
    if meta.logline:
        parts.append(f"Logline: {meta.logline}")
    if meta.opening:
        parts.append(f"Opening: {meta.opening}")
    if meta.ending:
        parts.append(f"Intended ending: {meta.ending}")
    if meta.references:
        parts.append(f"References and influences: {'; '.join(meta.references)}")
    if meta.notes:
        parts.append(f"Story notes: {meta.notes}")
    if meta.target_words:
        parts.append(f"Target length: about {meta.target_words:,} words")

    if meta.characters:
        parts.append("\nCharacters:")
        for character in meta.characters:
            line = f"- {character.name}"
            if character.role:
                line += f" ({character.role})"
            if character.description:
                line += f": {character.description}"
            parts.append(line)

    if include_outline and meta.outline:
        parts.append("\nChapter outline:")
        for number, entry in enumerate(meta.outline, start=1):
            line = f"{number}. {entry.title}"
            if entry.summary:
                line += f" — {entry.summary}"
            parts.append(line)
            if entry.direction:
                parts.append(f"   User direction: {entry.direction}")
    elif meta.outline:
        # When replanning, keep the user's guidance even though the old beats
        # are intentionally hidden from the editor.
        directions = [
            f"{number}. {entry.title}: {entry.direction}"
            for number, entry in enumerate(meta.outline, start=1)
            if entry.direction
        ]
        if directions:
            parts.append("\nUser directions from the existing plan:")
            parts.extend(directions)

    return "\n".join(parts)


def outline_prompt(meta: StoryMeta, chapters: int, premise: str = "") -> str:
    lines = [
        f"Build a {chapters}-chapter outline for the story described in the bible.",
    ]
    if premise:
        lines.append(f"\nUser premise for this outline:\n{premise}")
    lines.extend(
        [
            "",
            "Requirements:",
            f"- Return exactly {chapters} numbered lines, starting at 1.",
            "- Each line must use the format: `N. Chapter title — one-sentence summary`",
            "- Titles must be specific and evocative, not generic.",
            "- Each chapter must advance the plot and end on a meaningful turn.",
            "- No preamble, no commentary, no extra headings. Only the numbered lines.",
        ]
    )
    if meta.opening:
        lines.insert(-1, "- The first chapter must realize the opening in the bible.")
    if meta.ending:
        lines.insert(-1, "- The final chapter must land the intended ending in the bible.")
    return "\n".join(lines)


def chapter_prompt(
    meta: StoryMeta,
    number: int,
    entry: OutlineEntry,
    *,
    previous_tail: str = "",
    words: int = 1500,
) -> str:
    beat = entry.title
    if entry.summary:
        beat += f" — {entry.summary}"

    lines = [
        f"Write chapter {number} of {meta.title}.",
        "",
        f"Chapter beat: {beat}",
    ]
    if entry.direction:
        lines.extend(
            [
                "",
                (
                    "The user has directed what happens in this chapter. Follow "
                    "it closely; where it conflicts with the broad outline beat, "
                    "this direction takes precedence:"
                ),
                entry.direction.strip(),
            ]
        )
    if number == 1 and meta.opening:
        lines.extend(["", f"Requested opening: {meta.opening.strip()}"])
    if meta.ending and meta.outline and number == len(meta.outline):
        lines.extend(
            [
                "",
                f"Requested ending for the final chapter: {meta.ending.strip()}",
            ]
        )
    if previous_tail:
        continuity = (
            "Continue from that moment. Preserve continuity of detail, voice, "
            "and character knowledge."
        )
        lines.extend(
            [
                "",
                "The previous chapter ends with:",
                '"""',
                previous_tail.strip(),
                '"""',
                "",
                continuity,
            ]
        )
    else:
        opening = (
            "This is the opening chapter. Establish the world, the protagonist, "
            "and the inciting pressure without exposition dumps."
        )
        lines.extend(["", opening])
    closing = (
        "Write the full chapter now. Output prose only — no heading, no title, "
        "no notes."
    )
    lines.extend(["", f"Target length: about {words} words.", closing])
    return "\n".join(lines)


_NUMBERED_LINE = re.compile(r"^\s*(?:[-*]\s*)?(?:\*\*)?(\d+)[.)]\s*(.+?)\s*$")
_SEPARATORS = (" — ", " – ", " - ", ": ")


def parse_outline(text: str) -> list[OutlineEntry]:
    """Parse a numbered outline from model output.

    Tolerates markdown emphasis, headings, and trailing whitespace. The first
    line is not assumed to be chapter 1; entries keep document order.
    """
    entries: list[OutlineEntry] = []
    for raw in text.splitlines():
        line = raw.replace("**", "").replace("__", "").strip()
        if not line:
            continue
        match = _NUMBERED_LINE.match(line)
        if not match:
            continue
        rest = match.group(2).strip().strip("*")
        title, summary = rest, ""
        for separator in _SEPARATORS:
            if separator in rest:
                title, summary = rest.split(separator, 1)
                break
        title = title.strip().strip("\"'").rstrip(".").strip()
        summary = summary.strip()
        if title:
            entries.append(OutlineEntry(title=title, summary=summary))
    return entries
