"""Markdown export: concatenate the canonical chapter files with a title page."""

from __future__ import annotations

import json
from pathlib import Path

from ..store import StoryProject, slugify


def _yaml_string(value: str) -> str:
    return json.dumps(value)


def render_markdown(project: StoryProject, *, frontmatter: bool = False) -> str:
    meta = project.meta
    blocks: list[str] = []

    if frontmatter:
        lines = ["---", f"title: {_yaml_string(meta.title)}"]
        if meta.author:
            lines.append(f"author: {_yaml_string(meta.author)}")
        if meta.genre:
            lines.append(f"genre: {_yaml_string(meta.genre)}")
        if meta.logline:
            lines.append(f"description: {_yaml_string(meta.logline)}")
        lines.append("---")
        blocks.append("\n".join(lines))

    blocks.append(f"# {meta.title}")
    if meta.author:
        blocks.append(f"*by {meta.author}*")
    if meta.logline:
        blocks.append(f"> {meta.logline}")

    chapters = project.chapters()
    for chapter in chapters:
        blocks.append(chapter.text.rstrip())

    separator = "\n\n---\n\n"
    return separator.join(blocks).rstrip() + "\n"


def write_markdown(
    project: StoryProject,
    out: Path | None = None,
    *,
    frontmatter: bool = False,
) -> Path:
    if out is None:
        out = project.exports_dir / f"{slugify(project.meta.title)}.md"
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        render_markdown(project, frontmatter=frontmatter),
        encoding="utf-8",
        newline="\n",
    )
    return out
