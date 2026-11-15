"""Fountain export: a screenplay in plain text.

Fountain is the canonical format for screenplay stories, just as Markdown is
the canonical format for prose. Chapter documents are already stored as
Fountain; this module adds the title page and concatenates them into one
``.fountain`` script.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from ..store import StoryProject, slugify


def _title_page(project: StoryProject) -> str:
    meta = project.meta
    lines = [f"Title: {meta.title}"]
    if meta.author:
        lines.extend(["Credit: Written by", f"Author: {meta.author}"])
    if meta.genre:
        lines.append(f"Genre: {meta.genre}")
    if meta.logline:
        lines.append(f"Notes: {meta.logline}")
    lines.append(f"Draft date: {date.today():%Y-%m-%d}")
    return "\n".join(lines)


def render_fountain(project: StoryProject, *, title_page: bool = True) -> str:
    """Render the whole story as one Fountain document."""
    blocks: list[str] = []
    if title_page:
        blocks.append(_title_page(project))

    for chapter in project.chapters():
        blocks.append(chapter.text.rstrip())

    return "\n\n".join(blocks).rstrip() + "\n"


def write_fountain(project: StoryProject, out: Path | None = None) -> Path:
    if out is None:
        out = project.exports_dir / f"{slugify(project.meta.title)}.fountain"
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        render_fountain(project),
        encoding="utf-8",
        newline="\n",
    )
    return out
