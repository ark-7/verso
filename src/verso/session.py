"""Interactive story session.

This module holds the setup wizard and the plain prompt-loop fallback. In a
terminal with Textual installed, :func:`run_session` hands off to the
full-screen UI in :mod:`verso.tui`; the numbered menu here remains available
for ``--no-tui``, non-TTY environments, and minimal installs. Both paths write
preferences and directions to ``story.json`` as they are entered, so stopping a
session does not lose completed work.
"""

from __future__ import annotations

import importlib.util
import re
import sys

import typer
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .config import load_config
from .export.fountain import write_fountain
from .export.markdown import write_markdown
from .export.pdf import render_pdf
from .generate import (
    GenerationError,
    accept_review_suggestions,
    chapter_word_target,
    generate_outline,
    review_idea,
    write_chapter,
)
from .models import MODE_LABELS, POV, Character, IdeaReview, OutlineEntry, WritingMode
from .providers.base import Provider, ProviderError
from .store import StoryError, StoryProject

console = Console()

_MODE_KEYS = ("prose", "screenplay")
_TENSES = ("past", "present")
_POV_KEYS: dict[str, POV] = {
    "1": POV.first,
    "first": POV.first,
    "2": POV.third_limited,
    "third-limited": POV.third_limited,
    "third limited": POV.third_limited,
    "3": POV.third_omniscient,
    "third-omniscient": POV.third_omniscient,
    "third omniscient": POV.third_omniscient,
}


# --------------------------------------------------------------------- prompts
def _prompt_text(label: str, default: str = "") -> str:
    if default:
        answer = typer.prompt(label, default=default)
    else:
        answer = typer.prompt(label, default="", show_default=False)
    return str(answer).strip()


def _prompt_optional(label: str, default: str = "") -> str:
    """Prompt for a field that can be cleared by entering a lone dash."""
    value = _prompt_text(f"{label} ('-' to clear)", default)
    return "" if value == "-" else value


def _prompt_int(
    label: str,
    default: int,
    *,
    minimum: int = 1,
    maximum: int = 100,
) -> int:
    while True:
        raw = _prompt_text(label, str(default))
        try:
            value = int(raw)
        except ValueError:
            console.print(f"[red]{label} must be a whole number.[/red]")
            continue
        if minimum <= value <= maximum:
            return value
        console.print(
            f"[red]{label} must be between {minimum} and {maximum}.[/red]"
        )


def _prompt_choice(label: str, choices: tuple[str, ...], default: str) -> str:
    while True:
        raw = _prompt_text(f"{label} ({'/'.join(choices)})", default).lower()
        if raw in choices:
            return raw
        console.print(f"[red]Choose one of: {', '.join(choices)}.[/red]")


def _prompt_bool(label: str, default: bool) -> bool:
    default_text = "y" if default else "n"
    while True:
        raw = _prompt_text(f"{label} (y/n)", default_text).lower()
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        console.print("[red]Answer y or n.[/red]")


def _prompt_word_target(current: int | None) -> int | None:
    """Prompt for a per-chapter target; a lone dash clears the override."""
    while True:
        raw = _prompt_text(
            "Word target for this chapter ('-' for the story default)",
            str(current) if current else "",
        )
        if raw == "-":
            return None
        if not raw:
            return current
        try:
            value = int(raw)
        except ValueError:
            console.print("[red]Word target must be a whole number.[/red]")
            continue
        if value < 100:
            console.print("[red]Word target must be at least 100.[/red]")
            continue
        return value


def _chapter_goal(project: StoryProject) -> int:
    return project.meta.target_chapters or len(project.meta.outline) or 10


def _chapter_words(project: StoryProject, number: int) -> int:
    return chapter_word_target(
        project, number, fallback=load_config().words_per_chapter
    )


def _formats(project: StoryProject) -> tuple[str, ...]:
    return ("fountain", "pdf") if project.is_screenplay else ("md", "pdf")


def _prompt_pov(default: POV) -> POV:
    console.print(
        "[dim]1. First person   2. Third person limited   "
        "3. Third person omniscient[/dim]"
    )
    keys = {
        POV.first: "1",
        POV.third_limited: "2",
        POV.third_omniscient: "3",
    }
    while True:
        raw = _prompt_text("Point of view", keys[default]).lower()
        if raw in _POV_KEYS:
            return _POV_KEYS[raw]
        console.print("[red]Choose 1, 2, or 3.[/red]")


# -------------------------------------------------------------------- display
def _show_preferences(project: StoryProject) -> None:
    meta = project.meta
    console.print(f"\n[bold]{escape(meta.title)}[/bold]")
    table = Table(box=None, pad_edge=False)
    table.add_column("Preference", style="bold")
    table.add_column("Value")
    rows = [
        ("Author", meta.author),
        ("Genre", meta.genre),
        ("Tone", meta.tone),
        ("Writing mode", MODE_LABELS[meta.mode]),
        ("Point of view", meta.pov.value.replace("-", " ").capitalize()),
        ("Tense", meta.tense),
        ("Logline", meta.logline),
        ("Opening", meta.opening),
        ("Ending", meta.ending),
        ("References", "; ".join(meta.references)),
        ("Story notes", meta.notes),
        (
            "Chapters",
            str(meta.target_chapters or len(meta.outline) or 10),
        ),
        (
            "Words per chapter",
            f"{meta.words_per_chapter:,}" if meta.words_per_chapter else "from config",
        ),
        ("Chapter continuity", "on" if meta.continuity else "off"),
        (
            "Idea review",
            (
                f"{len(meta.idea_review.suggestions)} suggestion(s), "
                f"{len(meta.accepted_suggestions)} accepted"
                if meta.idea_review.reviewed_at
                else "not reviewed"
            ),
        ),
        ("Export format", meta.export_format),
    ]
    for label, value in rows:
        table.add_row(label, escape(value) if value else "[dim]not set[/dim]")
    console.print(table)


def _show_characters(project: StoryProject) -> None:
    characters = project.meta.characters
    if not characters:
        console.print("[dim]No characters yet.[/dim]")
        return
    table = Table(box=None, pad_edge=False)
    table.add_column("#", justify="right", style="dim")
    table.add_column("Name", style="bold")
    table.add_column("Role")
    table.add_column("Description", style="dim")
    for number, character in enumerate(characters, start=1):
        table.add_row(
            str(number),
            escape(character.name),
            escape(character.role) or "[dim]-[/dim]",
            escape(character.description) or "[dim]-[/dim]",
        )
    console.print(table)


def _show_outline(project: StoryProject) -> None:
    meta = project.meta
    if not meta.outline:
        console.print(
            "[dim]No outline yet. Choose 7 to generate one, or use "
            "`verso outline`.[/dim]"
        )
        return

    written = {chapter.number: chapter for chapter in project.chapters()}
    table = Table(box=None, pad_edge=False)
    table.add_column("#", justify="right", style="dim")
    table.add_column("Chapter", style="bold", overflow="fold")
    table.add_column("Beat", style="dim", overflow="fold")
    table.add_column("Your direction", overflow="fold")
    table.add_column("Target", justify="right")
    table.add_column("Status", justify="right")
    for number, entry in enumerate(meta.outline, start=1):
        chapter = written.get(number)
        status = (
            f"[green]{chapter.words:,} words[/green]"
            if chapter
            else "[dim]not written[/dim]"
        )
        target = f"{_chapter_words(project, number):,}"
        if entry.words:
            target = f"[bold]{target}[/bold]"
        table.add_row(
            str(number),
            escape(entry.title),
            escape(entry.summary) or "[dim]-[/dim]",
            escape(entry.direction) or "[dim]-[/dim]",
            target,
            status,
        )
    console.print(table)


def _show_progress(project: StoryProject) -> None:
    _show_preferences(project)
    if project.meta.characters:
        console.print("\n[bold]Characters[/bold]")
        _show_characters(project)
    console.print("\n[bold]Chapter plan[/bold]")
    _show_outline(project)
    done = len(project.chapters())
    planned = len(project.meta.outline)
    if planned:
        console.print(f"\n[dim]{done}/{planned} planned chapters written.[/dim]")


# ----------------------------------------------------------------- characters
def _choose_character(project: StoryProject, prompt: str) -> int | None:
    characters = project.meta.characters
    if not characters:
        console.print("[dim]No characters yet.[/dim]")
        return None
    while True:
        raw = _prompt_text(prompt, "1")
        if raw == "":
            return None
        try:
            number = int(raw)
        except ValueError:
            console.print("[red]Enter a character number, or 0 to cancel.[/red]")
            continue
        if number == 0:
            return None
        if 1 <= number <= len(characters):
            return number - 1
        console.print(f"[red]Choose 1–{len(characters)}, or 0 to cancel.[/red]")


def _add_character(project: StoryProject) -> None:
    name = _prompt_text("Character name")
    if not name:
        console.print("[dim]No character added.[/dim]")
        return
    existing = {character.name.casefold() for character in project.meta.characters}
    if name.casefold() in existing:
        console.print(f"[red]{escape(name)} is already in the character list.[/red]")
        return
    role = _prompt_optional("Role", "")
    description = _prompt_optional("Description", "")
    project.meta.characters.append(
        Character(name=name, role=role, description=description)
    )
    project.save_meta()
    console.print(f"[green]Added[/green] {escape(name)}.")


def _edit_character(project: StoryProject) -> None:
    index = _choose_character(project, "Character number to edit (0 to cancel)")
    if index is None:
        return
    character = project.meta.characters[index]
    name = _prompt_text("Name", character.name) or character.name
    character.name = name
    character.role = _prompt_optional("Role", character.role)
    character.description = _prompt_optional("Description", character.description)
    project.save_meta()
    console.print(f"[green]Updated[/green] {escape(character.name)}.")


def _remove_character(project: StoryProject) -> None:
    index = _choose_character(project, "Character number to remove (0 to cancel)")
    if index is None:
        return
    character = project.meta.characters[index]
    if not typer.confirm(f"Remove {character.name}?", default=False):
        return
    project.meta.characters.pop(index)
    project.save_meta()
    console.print(f"[green]Removed[/green] {escape(character.name)}.")


def _manage_characters(project: StoryProject) -> None:
    console.print("\n[bold]Characters[/bold]")
    while True:
        _show_characters(project)
        default = "1" if not project.meta.characters else "0"
        console.print(
            "[dim]1. Add   2. Edit   3. Remove   0. Done[/dim]"
        )
        choice = _prompt_text("Characters", default)
        if choice in {"0", "d", "done", ""}:
            return
        if choice == "1":
            _add_character(project)
        elif choice == "2":
            _edit_character(project)
        elif choice == "3":
            _remove_character(project)
        else:
            console.print("[red]Choose 0–3.[/red]")


# ------------------------------------------------------------------ preferences
def _edit_preferences(project: StoryProject) -> None:
    meta = project.meta
    console.print(
        "\n[bold]Story preferences[/bold] "
        "[dim](Enter keeps the current value; '-' clears optional fields)[/dim]"
    )
    try:
        meta.title = _prompt_text("Title", meta.title) or meta.title
        meta.author = _prompt_optional("Author", meta.author)
        meta.genre = _prompt_optional("Genre", meta.genre)
        meta.tone = _prompt_optional("Tone", meta.tone)
        meta.logline = _prompt_optional("Logline or premise", meta.logline)
        meta.pov = _prompt_pov(meta.pov)
        tense_default = meta.tense if meta.tense in _TENSES else "past"
        meta.tense = _prompt_choice("Tense", _TENSES, tense_default)
        meta.opening = _prompt_optional(
            "How should the story start?", meta.opening
        )
        meta.ending = _prompt_optional("How should it end?", meta.ending)
        current_references = ", ".join(meta.references)
        references = _prompt_optional(
            "References, inspirations, or source material, comma-separated",
            current_references,
        )
        meta.references = [
            part.strip() for part in references.split(",") if part.strip()
        ]
        meta.notes = _prompt_optional("Story ideas or notes", meta.notes)
        words_default = (
            meta.words_per_chapter or load_config().words_per_chapter
        )
        meta.words_per_chapter = _prompt_int(
            "Words per chapter",
            words_default,
            minimum=100,
            maximum=20000,
        )
        meta.continuity = _prompt_bool(
            "Keep each chapter continuous with the previous one?",
            meta.continuity,
        )
        meta.target_chapters = _prompt_int(
            "Number of chapters", _chapter_goal(project), minimum=1, maximum=100
        )
        mode = _prompt_choice(
            "Writing mode", _MODE_KEYS, meta.mode.value
        )
        meta.mode = WritingMode(mode)
        formats = _formats(project)
        format_default = (
            meta.export_format if meta.export_format in formats else formats[0]
        )
        meta.export_format = _prompt_choice(
            "Export format", formats, format_default
        )
    finally:
        # Ctrl-C in the middle of the wizard should still keep what was entered.
        project.save_meta()
    console.print("[green]Saved[/green] story preferences.")
    _show_preferences(project)


# --------------------------------------------------------------------- outline
def _regenerate_outline(
    project: StoryProject,
    llm: Provider,
    *,
    chapters: int,
    premise: str = "",
    confirm_replace: bool = True,
) -> None:
    if project.meta.outline and confirm_replace:
        if not typer.confirm(
            "Replace the current chapter plan?", default=False
        ):
            return
    if project.chapter_paths():
        console.print(
            "[yellow]Note:[/yellow] already-written chapters stay on disk, "
            "but their planned beats will change."
        )
    with console.status("Drafting the chapter plan...", spinner="dots"):
        entries = generate_outline(
            project, llm, chapters=chapters, premise=premise
        )
    console.print(
        f"[green]Saved[/green] {len(entries)} planned chapters."
    )
    _show_outline(project)


# -------------------------------------------------------------------- chapters
def _prompt_chapter_number(project: StoryProject) -> int:
    count = len(project.meta.outline)
    if not count:
        raise GenerationError(
            "No outline yet. Generate one first (menu option 7)."
        )
    return _prompt_int("Chapter number", 1, minimum=1, maximum=count)


def _set_direction(project: StoryProject, entry: OutlineEntry) -> str:
    direction = _prompt_optional(
        "What should happen in this chapter? (optional)", entry.direction
    )
    if direction != entry.direction:
        entry.direction = direction
        project.save_meta()
    return entry.direction


def _write_chapter_interactive(
    project: StoryProject,
    llm: Provider,
    number: int,
    *,
    force: bool = False,
) -> None:
    entry = project.meta.outline[number - 1]
    target = _chapter_words(project, number)
    target_path = project.chapter_path_for(number, entry.title)
    if force:
        # A renamed chapter would otherwise leave two files with the same number.
        for chapter in project.chapters():
            if chapter.number == number and chapter.path != target_path:
                chapter.path.unlink(missing_ok=True)

    console.print(f"\n[bold]Chapter {number}: {escape(entry.title)}[/bold]")
    console.print(
        f"[dim]Writing ~{target:,} words with {escape(llm.name)}/"
        f"{escape(llm.model)}. Ctrl-C keeps the partial text.[/dim]\n"
    )

    def emit(token: str) -> None:
        sys.stdout.write(token)
        sys.stdout.flush()

    result = write_chapter(
        project, llm, number, words=target, force=force, on_token=emit
    )
    console.print(
        f"\n[green]Saved[/green] {escape(str(result.path))} · "
        f"{result.words:,} words"
    )


def _existing_chapter(project: StoryProject, number: int):
    return next(
        (chapter for chapter in project.chapters() if chapter.number == number),
        None,
    )


def _write_next(project: StoryProject, llm: Provider) -> None:
    outline = project.meta.outline
    if not outline:
        raise GenerationError(
            "No outline yet. Generate one first (menu option 7)."
        )
    written = {chapter.number for chapter in project.chapters()}
    number = next(
        (candidate for candidate in range(1, len(outline) + 1) if candidate not in written),
        None,
    )
    if number is None:
        console.print(
            "[green]Every planned chapter is written.[/green] "
            "Choose 2 to rewrite one, 7 to plan more, or 8 to export."
        )
        return
    entry = outline[number - 1]
    console.print(
        f"\n[bold]Up next: chapter {number} — {escape(entry.title)}[/bold]"
    )
    if entry.summary:
        console.print(f"[dim]{escape(entry.summary)}[/dim]")
    _set_direction(project, entry)
    _write_chapter_interactive(project, llm, number)


def _write_specific(project: StoryProject, llm: Provider) -> None:
    number = _prompt_chapter_number(project)
    entry = project.meta.outline[number - 1]
    existing = _existing_chapter(project, number)
    force = False
    if existing is not None:
        if not typer.confirm(
            f"Chapter {number} already exists. Overwrite it?", default=False
        ):
            return
        force = True
    console.print(
        f"\n[bold]Chapter {number}: {escape(entry.title)}[/bold]"
    )
    if entry.summary:
        console.print(f"[dim]{escape(entry.summary)}[/dim]")
    _set_direction(project, entry)
    _write_chapter_interactive(project, llm, number, force=force)


def _guide_chapter(project: StoryProject) -> None:
    number = _prompt_chapter_number(project)
    entry = project.meta.outline[number - 1]
    console.print(
        f"\n[bold]Chapter {number}: {escape(entry.title)}[/bold]"
    )
    if entry.summary:
        console.print(f"[dim]{escape(entry.summary)}[/dim]")
    direction = _set_direction(project, entry)
    if direction:
        console.print(
            f"[green]Direction saved[/green] for chapter {number}."
        )
    else:
        console.print(f"[dim]Direction cleared for chapter {number}.[/dim]")


def _edit_outline(project: StoryProject) -> None:
    number = _prompt_chapter_number(project)
    entry = project.meta.outline[number - 1]
    console.print(
        f"\n[bold]Editing chapter {number}[/bold]"
    )
    try:
        entry.title = _prompt_text("Title", entry.title) or entry.title
        entry.summary = _prompt_optional("Beat summary", entry.summary)
        entry.direction = _prompt_optional("Your direction", entry.direction)
        entry.words = _prompt_word_target(entry.words)
    finally:
        project.save_meta()
    console.print("[green]Saved[/green] chapter plan.")
    _show_outline(project)


def _set_chapter_words(project: StoryProject) -> None:
    number = _prompt_chapter_number(project)
    entry = project.meta.outline[number - 1]
    console.print(
        f"\n[bold]Chapter {number}: {escape(entry.title)}[/bold] — "
        f"currently ~{_chapter_words(project, number):,} words"
    )
    entry.words = _prompt_word_target(entry.words)
    project.save_meta()
    console.print(
        f"[green]Saved[/green] chapter {number} now targets "
        f"~{_chapter_words(project, number):,} words."
    )


# -------------------------------------------------------------------- review
def _show_review(review: IdeaReview) -> None:
    console.print(
        Panel(
            Text(review.text),
            title="Idea review — your idea is primary",
            border_style="cyan",
            padding=(0, 1),
        )
    )
    if not review.suggestions:
        console.print(
            "[dim]No optional suggestions. Your idea stands as written.[/dim]"
        )
        return
    table = Table(box=None, pad_edge=False)
    table.add_column("#", justify="right", style="dim")
    table.add_column("Optional suggestion", overflow="fold")
    for number, suggestion in enumerate(review.suggestions, start=1):
        table.add_row(str(number), escape(suggestion))
    console.print(table)


def _parse_review_choice(answer: str, count: int) -> tuple[list[int], str]:
    """Split an accept prompt into suggestion indexes and author wording."""
    text = answer.strip()
    if not text:
        return [], ""
    if text.lower() in {"all", "*"}:
        return list(range(1, count + 1)), ""
    parts = [part for part in re.split(r"[,\s]+", text) if part]
    if parts and all(part.isdigit() for part in parts):
        return [int(part) for part in parts], ""
    return [], text


def _review_story_idea(project: StoryProject, llm: Provider) -> None:
    premise = project.meta.logline or project.meta.notes
    with console.status("Reviewing your idea...", spinner="dots"):
        review = review_idea(project, llm, premise=premise)
    _show_review(review)
    if not review.suggestions:
        return
    answer = _prompt_text(
        "Accept suggestions (e.g. 1,3 or 'all'; type your own wording to "
        "enhance; Enter keeps your idea unchanged)",
        "",
    )
    indexes, enhancement = _parse_review_choice(answer, len(review.suggestions))
    accepted = accept_review_suggestions(
        project, indexes, enhancement=enhancement
    )
    if accepted:
        console.print(
            f"[green]Accepted[/green] {len(accepted)} item(s). "
            "They now guide later generations; the original idea is unchanged."
        )
    else:
        console.print("[dim]Your idea is unchanged.[/dim]")


# ----------------------------------------------------------------------- export
def _export(project: StoryProject) -> None:
    formats = _formats(project)
    default = (
        project.meta.export_format
        if project.meta.export_format in formats
        else formats[0]
    )
    fmt = _prompt_choice("Export format", formats, default)
    project.meta.export_format = fmt
    project.save_meta()
    if fmt == "pdf":
        path = render_pdf(project)
    elif fmt == "fountain":
        path = write_fountain(project)
    else:
        path = write_markdown(project)
    console.print(
        f"[green]Exported[/green] {escape(str(path))} "
        f"({path.stat().st_size:,} bytes)"
    )


# ------------------------------------------------------------------------ setup
def _setup_story(project: StoryProject, llm: Provider) -> None:
    console.print(
        "\n[bold]Let's set up the story.[/bold] "
        "You can change any of this later from the session menu."
    )
    _edit_preferences(project)
    _manage_characters(project)

    if typer.confirm(
        "Review your story idea before planning? Your idea stays primary.",
        default=True,
    ):
        try:
            _review_story_idea(project, llm)
        except (GenerationError, ProviderError) as exc:
            console.print(
                f"[red]Could not review the idea:[/red] {escape(str(exc))}"
            )
            console.print(
                "[dim]Your idea and preferences are saved. You can retry with "
                "menu option 10.[/dim]"
            )

    chapters = _chapter_goal(project)
    if not project.meta.outline:
        if typer.confirm("Generate the chapter plan now?", default=True):
            try:
                _regenerate_outline(
                    project,
                    llm,
                    chapters=chapters,
                    premise=project.meta.logline,
                    confirm_replace=False,
                )
            except (GenerationError, ProviderError) as exc:
                console.print(
                    f"[red]Could not generate the outline:[/red] "
                    f"{escape(str(exc))}"
                )
                console.print(
                    "[dim]Your preferences were saved. You can retry "
                    "from menu option 7.[/dim]"
                )
    elif typer.confirm(
        "Regenerate the chapter plan with these preferences?", default=False
    ):
        try:
            _regenerate_outline(
                project,
                llm,
                chapters=chapters,
                premise=project.meta.logline,
            )
        except (GenerationError, ProviderError) as exc:
            console.print(
                f"[red]Could not regenerate the outline:[/red] "
                f"{escape(str(exc))}"
            )


_MENU = """
[bold]Session menu[/bold]
  1. Write the next chapter
  2. Write a specific chapter
  3. Guide what happens in a chapter
  4. Edit the chapter plan
  5. Manage characters
  6. Review story preferences
  7. Regenerate the chapter plan
  8. Export the story
  9. Show progress
 10. Review your story idea (advice only)
 11. Set a chapter's word target
  q. End the session
"""


def _tui_available() -> bool:
    """The full-screen UI needs a real terminal and an installed Textual."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    return importlib.util.find_spec("textual") is not None


def run_session(
    project: StoryProject,
    llm: Provider,
    *,
    setup: bool | None = None,
    tui: bool | None = None,
) -> None:
    """Run the interactive session until the user quits.

    ``tui`` defaults to on when the terminal and Textual both allow it. When it
    is off, or Textual is missing, the plain prompt loop below is used instead.
    """
    launcher = None
    if tui is None:
        tui = _tui_available()
    if tui:
        try:
            from .tui import launch_tui as launcher
        except ImportError:
            console.print(
                "[yellow]Textual is not installed; using the plain session.[/yellow]"
            )
            tui = False

    if not tui:
        console.print(
            f"\n[bold]Verso interactive session[/bold] · "
            f"{escape(project.meta.title)}"
        )
        console.print(
            f"[dim]{escape(str(project.root))} · "
            f"{MODE_LABELS[project.meta.mode]} · "
            f"{escape(llm.name)}/{escape(llm.model)}[/dim]"
        )

    if setup is None:
        setup = not project.meta.outline
    if setup:
        try:
            _setup_story(project, llm)
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Setup stopped. Saved preferences are kept.[/dim]")
            return

    if launcher is not None:
        launcher(project, llm)
        return

    while True:
        console.print(_MENU)
        try:
            choice = _prompt_text("What next?", "9").lower()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Session ended.[/dim]")
            return
        if choice in {"q", "quit", "exit"}:
            console.print("[dim]Session ended.[/dim]")
            return
        try:
            if choice == "1":
                _write_next(project, llm)
            elif choice == "2":
                _write_specific(project, llm)
            elif choice == "3":
                _guide_chapter(project)
            elif choice == "4":
                _edit_outline(project)
            elif choice == "5":
                _manage_characters(project)
            elif choice == "6":
                _edit_preferences(project)
            elif choice == "7":
                _regenerate_outline(
                    project,
                    llm,
                    chapters=_chapter_goal(project),
                    premise=project.meta.logline,
                )
            elif choice == "8":
                _export(project)
            elif choice == "9":
                _show_progress(project)
            elif choice == "10":
                _review_story_idea(project, llm)
            elif choice == "11":
                _set_chapter_words(project)
            else:
                console.print("[red]Choose 1–11 or q.[/red]")
        except (GenerationError, ProviderError, StoryError) as exc:
            console.print(f"[red]Error:[/red] {escape(str(exc))}")
        except KeyboardInterrupt:
            console.print("\n[dim]Cancelled.[/dim]")
        except EOFError:
            console.print("\n[dim]Session ended.[/dim]")
            return


__all__ = ["run_session"]
