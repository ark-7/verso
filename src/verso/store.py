"""Story project storage: a directory with ``story.json`` + ``chapters/*.md``.

Markdown is the canonical format. Everything else (PDF, outlines) is derived.
Writes are atomic so a crash never leaves a half-written metadata file, and the
chapter generator streams into a ``.part`` file before promoting it.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .models import StoryMeta, WritingMode

META_FILE = "story.json"
CHAPTERS_DIR = "chapters"
EXPORTS_DIR = "exports"
CHAPTER_SUFFIXES = (".md", ".fountain")


class StoryError(Exception):
    """A user-facing problem with the story project on disk."""


class AmbiguousStoryError(StoryError):
    """Several nearby story projects were detected and none was specified."""

    def __init__(self, message: str, candidates: list[Path]) -> None:
        super().__init__(message)
        self.candidates = candidates


# Directories that never contain story projects worth scanning.
_SKIP_DIRS = {
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    "venv",
    "chapters",
    "exports",
}


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "untitled"


@dataclass(frozen=True)
class Chapter:
    number: int
    path: Path
    title: str
    body: str
    text: str

    @property
    def words(self) -> int:
        return len(self.body.split())


def _split_heading(text: str, number: int) -> tuple[str, str]:
    """Separate the leading Markdown H1 from the chapter prose."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        stripped = line.lstrip()
        if stripped.startswith("# "):
            title = stripped[2:].strip() or f"Chapter {number}"
            return title, "\n".join(lines[index + 1 :]).lstrip("\n")
        break
    return f"Chapter {number}", text


def _number_from_name(name: str) -> int | None:
    match = re.match(r"^(\d+)-", name)
    return int(match.group(1)) if match else None


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


class StoryProject:
    def __init__(self, root: Path, meta: StoryMeta) -> None:
        self.root = Path(root)
        self.meta = meta

    # ------------------------------------------------------------------ create
    @classmethod
    def create(
        cls,
        root: Path,
        *,
        title: str,
        author: str = "",
        genre: str = "",
        premise: str = "",
        mode: WritingMode | str = WritingMode.prose,
    ) -> StoryProject:
        root = Path(root)
        if (root / META_FILE).exists():
            raise StoryError(f"A story already exists at {root}.")
        (root / CHAPTERS_DIR).mkdir(parents=True, exist_ok=True)
        (root / EXPORTS_DIR).mkdir(parents=True, exist_ok=True)
        mode = WritingMode(mode)
        meta = StoryMeta(
            title=title,
            author=author,
            genre=genre,
            logline=premise,
            mode=mode,
            export_format="fountain" if mode == WritingMode.screenplay else "md",
        )
        project = cls(root, meta)
        project.save_meta()
        return project

    # --------------------------------------------------------------- discovery
    @staticmethod
    def _load_meta(meta_path: Path) -> StoryMeta:
        try:
            return StoryMeta.model_validate_json(
                meta_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise StoryError(f"Could not read {meta_path}: {exc}") from exc

    @classmethod
    def find_here(cls, start: Path | None = None) -> StoryProject | None:
        """Find a story at `start` or any of its ancestors."""
        path = Path(start or Path.cwd()).expanduser().resolve()
        if path.is_file():
            path = path.parent
        for candidate in (path, *path.parents):
            meta_path = candidate / META_FILE
            if meta_path.is_file():
                return cls(candidate, cls._load_meta(meta_path))
        return None

    @classmethod
    def load(cls, start: Path | None = None) -> StoryProject:
        project = cls.find_here(start)
        if project is None:
            raise StoryError(
                "No story found here. Run `verso new <title>` first, "
                "or pass `--story <path>`."
            )
        return project

    @classmethod
    def discover(
        cls, start: Path | None = None, *, depth: int = 2
    ) -> list[StoryProject]:
        """Find story projects at or below `start`, up to `depth` levels deep.

        Hidden directories and common build/dependency directories are skipped,
        and a story project is never descended into.
        """
        root = Path(start or Path.cwd()).expanduser().resolve()
        if root.is_file():
            root = root.parent

        projects: list[StoryProject] = []
        seen: set[Path] = set()
        queue: list[tuple[Path, int]] = [(root, 0)]
        while queue:
            current, level = queue.pop(0)
            resolved = current.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)

            meta_path = current / META_FILE
            if meta_path.is_file():
                try:
                    projects.append(cls(current, cls._load_meta(meta_path)))
                except StoryError:
                    pass  # skip malformed projects during discovery
                continue

            if level >= depth:
                continue
            try:
                children = sorted(current.iterdir())
            except OSError:
                continue
            for child in children:
                if (
                    child.is_dir()
                    and child.name not in _SKIP_DIRS
                    and not child.name.startswith(".")
                ):
                    queue.append((child, level + 1))

        projects.sort(
            key=lambda project: (
                project.meta.title.casefold(),
                str(project.root).casefold(),
            )
        )
        return projects

    @classmethod
    def resolve(cls, start: Path | None = None, *, depth: int = 2) -> StoryProject:
        """Locate a story: current directory/ancestors first, then nearby ones."""
        project = cls.find_here(start)
        if project is not None:
            return project

        candidates = cls.discover(start, depth=depth)
        if not candidates:
            raise StoryError(
                "No story found here or in nearby directories. "
                "Run `verso new <title>` first, or pass `--story <path>`."
            )
        if len(candidates) == 1:
            return candidates[0]

        listing = "\n".join(
            f"  - {candidate.root} ({candidate.meta.title})"
            for candidate in candidates
        )
        raise AmbiguousStoryError(
            f"Multiple stories found nearby:\n{listing}\n"
            "Pass `--story <path>` to choose one.",
            [candidate.root for candidate in candidates],
        )

    def save_meta(self) -> None:
        self.meta.updated_at = datetime.now(UTC)
        _atomic_write(
            self.root / META_FILE,
            self.meta.model_dump_json(indent=2) + "\n",
        )

    # ---------------------------------------------------------------- chapters
    @property
    def chapters_dir(self) -> Path:
        return self.root / CHAPTERS_DIR

    @property
    def exports_dir(self) -> Path:
        return self.root / EXPORTS_DIR

    @property
    def chapter_suffix(self) -> str:
        """The canonical extension for new chapters in this story's mode."""
        return (
            ".fountain"
            if self.meta.mode == WritingMode.screenplay
            else ".md"
        )

    @property
    def is_screenplay(self) -> bool:
        return self.meta.mode == WritingMode.screenplay

    def chapter_paths(self) -> list[Path]:
        if not self.chapters_dir.is_dir():
            return []
        paths: list[Path] = []
        for suffix in CHAPTER_SUFFIXES:
            paths.extend(self.chapters_dir.glob(f"*{suffix}"))
        return sorted(paths)

    def chapters(self) -> list[Chapter]:
        chapters = []
        for position, path in enumerate(self.chapter_paths(), start=1):
            number = _number_from_name(path.name) or position
            text = path.read_text(encoding="utf-8")
            title, body = _split_heading(text, number)
            chapters.append(Chapter(number, path, title, body, text))
        return chapters

    def next_chapter_number(self) -> int:
        numbers = [_number_from_name(path.name) for path in self.chapter_paths()]
        return max((number for number in numbers if number), default=0) + 1

    def chapter(self, number: int) -> Chapter:
        chapters = self.chapters()
        if number < 1 or number > len(chapters):
            raise StoryError(f"Chapter {number} does not exist (1–{len(chapters)}).")
        return chapters[number - 1]

    def chapter_path_for(self, number: int, title: str) -> Path:
        return self.chapters_dir / f"{number:03d}-{slugify(title)}{self.chapter_suffix}"

    def add_chapter(self, number: int, title: str, body: str = "") -> Chapter:
        path = self.chapter_path_for(number, title)
        text = f"# Chapter {number}: {title}\n\n{body}".rstrip() + "\n"
        _atomic_write(path, text)
        chapter_title, chapter_body = _split_heading(text, number)
        return Chapter(number, path, chapter_title, chapter_body, text)

    def chapter_heading(self, number: int, title: str) -> str:
        return f"# Chapter {number}: {title}\n\n"
