"""Prompt construction.

Kept in one module so the writing voice is tunable without touching the CLI or
the provider layer. The ``story_bible`` is deliberately stable between calls so
providers that support prompt caching can reuse the prefix.
"""

from __future__ import annotations

import re

from .models import POV_LABELS, OutlineEntry, StoryMeta, WritingMode

WRITER_SYSTEM = """You are a disciplined novelist working as a co-writer. \
Write publishable prose in the voice described by the story bible.

Rules:
- Follow the story bible exactly: point of view, tense, tone, characters, and \
established continuity.
- Show, don't tell. Use concrete sensory detail and natural dialogue.
- Never address the reader, explain your process, or mention being an AI.
- Do not include a chapter heading, title, notes, or commentary. Output prose only.
"""

SCREENWRITER_SYSTEM = """You are a professional screenwriter working as a \
co-writer. Write a screenplay in Fountain plain-text format, in the voice and \
genre described by the story bible.

Fountain rules:
- Scene headings are uppercase and start with INT., EXT., INT./EXT., or EST. \
Example: INT. FERRY - NIGHT
- Action lines are present tense, visual, and written in normal sentence case.
- Character cues are uppercase on a line of their own, with dialogue directly \
beneath. Parentheticals go on their own line in parentheses.
- Transitions are uppercase and end in TO: (CUT TO:) or start a line with >.
- Separate every element with a single blank line. Do not indent.
- Never use Markdown: no #, **, *, bullet lists, or code fences.
- Do not include a title page, scene numbers, act numbers, notes, or commentary.
- Follow the story bible exactly: tone, characters, and established continuity.
- Output Fountain text only.
"""

EDITOR_SYSTEM = """You are a story editor. You design tight, causally linked \
chapter outlines in which each chapter changes the situation and pushes the \
protagonist toward the climax. You write concise, concrete beats, never vague \
promises. Follow the story bible exactly.
"""

REVIEWER_SYSTEM = """You are a story editor reviewing an author's idea. The \
author's idea is primary. Your job is to help them see it more clearly and to \
offer optional refinements, never to rewrite or replace it.

Rules:
- Treat everything the author supplied as intent, not as a draft to correct.
- Start with what already works and why it works.
- Ask at most three specific questions about stakes, causality, character \
desire, and obstacles. Ask; do not answer them yourself.
- Offer suggestions only as options. Every suggestion must build on the \
author's premise and preserve their voice and genre.
- Never say the author must change something, and never imply a suggestion is \
already accepted. The author decides what to use.
- Do not write scenes, dialogue, or a replacement outline.
- Output plain text under exactly three headings: What works, Questions, \
Optional suggestions.
- Number the optional suggestions 1., 2., 3. so the author can accept them \
individually. If the idea is already strong, it is fine to offer fewer.
"""


def story_bible(meta: StoryMeta, *, include_outline: bool = True) -> str:
    parts = ["# Story bible", f"Title: {meta.title}"]
    parts.append(
        "Format: screenplay, written in Fountain plain text"
        if meta.mode == WritingMode.screenplay
        else "Format: prose fiction"
    )
    if meta.genre:
        parts.append(f"Genre: {meta.genre}")
    if meta.tone:
        parts.append(f"Tone: {meta.tone}")
    parts.append(f"Point of view: {POV_LABELS[meta.pov]}")
    if meta.mode == WritingMode.screenplay:
        parts.append("Tense: present for action and scene description")
    else:
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
    if meta.accepted_suggestions:
        parts.append("\nAuthor-approved suggestions (from an editor review):")
        parts.extend(f"- {suggestion}" for suggestion in meta.accepted_suggestions)

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
    if meta.mode == WritingMode.screenplay:
        lines.insert(
            -1,
            "- This is a screenplay: treat each chapter as a sequence of 2–4 "
            "scenes, and make each beat visual and playable.",
        )
    if meta.opening:
        lines.insert(-1, "- The first chapter must realize the opening in the bible.")
    if meta.ending:
        lines.insert(-1, "- The final chapter must land the intended ending in the bible.")
    return "\n".join(lines)


def review_prompt(meta: StoryMeta, premise: str = "") -> str:
    """Ask for advisory feedback on the author's idea.

    The author's own wording is quoted verbatim so the reviewer responds to the
    idea as given rather than to a paraphrase.
    """
    idea = premise.strip() or meta.logline.strip() or meta.notes.strip()
    lines = [
        "Review the author's story idea below. The author's idea is primary.",
        "",
        f"Title: {meta.title}",
    ]
    if idea:
        lines.extend(["", "The author's idea, in their own words:", '"""', idea, '"""'])
    else:
        lines.extend(
            [
                "",
                (
                    "The author's idea is described in the story bible above. "
                    "Base the review on that."
                ),
            ]
        )
    lines.extend(
        [
            "",
            (
                "Respond under exactly three headings: What works, Questions, "
                "Optional suggestions. Number the optional suggestions so the "
                "author can accept them individually. The author decides what, "
                "if anything, to use."
            ),
        ]
    )
    return "\n".join(lines)


_SUGGESTION_HEADING = re.compile(
    r"^(?:#+\s*|\*\*)?(?:optional\s+)?suggestions?\b", re.IGNORECASE
)
_OTHER_HEADING = re.compile(
    r"^(?:#+\s*|\*\*)?(?:what\s+works|questions?)\b", re.IGNORECASE
)
_NUMBERED_SUGGESTION = re.compile(r"^(?:\d+[.)]|[-*])\s+(.+)$")


def parse_suggestions(text: str) -> list[str]:
    """Extract numbered optional suggestions from a review."""
    suggestions: list[str] = []
    collecting = False
    for raw in text.splitlines():
        line = raw.strip().strip("*").strip()
        if not line:
            continue
        heading = re.sub(r"^\d+[.)]\s*", "", line)
        if _SUGGESTION_HEADING.match(heading):
            collecting = True
            continue
        if not collecting:
            continue
        if _OTHER_HEADING.match(heading):
            break
        match = _NUMBERED_SUGGESTION.match(line)
        if match:
            suggestion = match.group(1).strip().strip("*").strip()
            if suggestion:
                suggestions.append(suggestion)
    return suggestions


def chapter_prompt(
    meta: StoryMeta,
    number: int,
    entry: OutlineEntry,
    *,
    previous_tail: str = "",
    previous_number: int | None = None,
    previous_title: str = "",
    previous_summary: str = "",
    words: int = 1500,
    screenplay: bool | None = None,
) -> str:
    if screenplay is None:
        screenplay = meta.mode == WritingMode.screenplay

    beat = entry.title
    if entry.summary:
        beat += f" — {entry.summary}"

    opening_line = (
        f"Write chapter {number} of {meta.title} as a screenplay sequence."
        if screenplay
        else f"Write chapter {number} of {meta.title}."
    )
    lines = [opening_line, "", f"Chapter beat: {beat}"]
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
        beat = previous_title or ""
        if previous_summary:
            beat += f" — {previous_summary}" if beat else previous_summary
        if previous_number is not None:
            beat_line = f"Chapter {previous_number} was: {beat.strip()}"
            tail_line = f"Chapter {previous_number} ends with:"
        else:
            beat_line = f"The previous chapter was: {beat.strip()}"
            tail_line = "The previous chapter ends with:"
        lines.append("")
        if beat:
            lines.append(beat_line)
        lines.extend(
            [
                tail_line,
                '"""',
                previous_tail.strip(),
                '"""',
                "",
                (
                    "Open this chapter in direct continuity with that moment. "
                    "Keep the same time, place, characters, and what each "
                    "character knows. Advance the situation from there; do not "
                    "recap or repeat the previous text."
                ),
            ]
        )
        if screenplay:
            lines.append(
                "Keep the same scene logic and open with the next scene "
                "heading when the location or time changes."
            )
    else:
        opening = (
            "This is the opening sequence. Establish the world, the protagonist, "
            "and the inciting pressure visually, without exposition dumps."
            if screenplay
            else (
                "This is the opening chapter. Establish the world, the "
                "protagonist, and the inciting pressure without exposition dumps."
            )
        )
        lines.extend(["", opening])

    if screenplay:
        pages = max(1, round(words / 150))
        lines.extend(
            [
                "",
                (
                    f"Target length: about {pages} screenplay page(s), roughly "
                    f"{words} words."
                ),
                (
                    "Write the sequence now in Fountain plain text. Start with a "
                    "scene heading, not a title or chapter heading. Output "
                    "screenplay text only — no notes or commentary."
                ),
            ]
        )
    else:
        closing = (
            "Write the full chapter now. Output prose only — no heading, no "
            "title, no notes."
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
