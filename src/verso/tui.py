"""Full-screen interactive session UI.

This is the closer-to-Claude-Code skin for the writing session: a persistent
header, a scrollable transcript, a live streaming pane, and a single command
line at the bottom. The plain prompt loop in :mod:`verso.session` remains the
fallback for non-terminal use and for ``--no-tui``.
"""

from __future__ import annotations

import re
from difflib import get_close_matches
from typing import ClassVar

from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.css.query import NoMatches
from textual.widgets import Footer, Input, RichLog, Static

from .config import load_config
from .export.fountain import write_fountain
from .export.markdown import write_markdown
from .export.pdf import render_pdf
from .generate import (
    GenerationError,
    accept_review_suggestions,
    chapter_word_target,
    clear_accepted_suggestions,
    generate_outline,
    review_idea,
    write_chapter,
)
from .models import (
    MODE_LABELS,
    Character,
    IdeaReview,
    OutlineEntry,
    WritingMode,
)
from .providers.base import Provider, ProviderError
from .session import (
    _chapter_goal,
    _edit_preferences,
    _manage_characters,
    _setup_story,
)
from .store import Chapter, StoryError, StoryProject

_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_SCENE_HEADING = re.compile(r"^(?:INT|EXT|EST|INT\./EXT|I/E)[.\s]", re.IGNORECASE)
_COMMANDS = """
### Writing
| Command | What it does |
|---|---|
| `/write` or `/w` | Write the next unwritten chapter |
| `/write 3` | Write chapter 3 (asks about overwriting) |
| `/guide 3` | Set a direction for chapter 3 |
| `/edit 3` | Edit chapter 3's title, beat, and direction |
| `/outline` | Regenerate the plan; `/outline 12` for 12 chapters |
| `/words` | Show word targets; `/words 1200` sets the story default |
| `/words 3 900` | Set chapter 3 to ~900 words; `/words 3 -` clears it |

### Story
| Command | What it does |
|---|---|
| `/plan` | Show the chapter plan |
| `/progress` | Show preferences, characters, and progress |
| `/characters` | Show characters; `/characters manage` to edit them |
| `/prefs` | Show preferences; `/prefs edit` to change them |
| `/setup` | Re-run the full story setup wizard |
| `/notes` | Show story notes; `/notes <text>` appends to them |
| `/mode` | Show the writing mode; `/mode screenplay` to switch |
| `/continuity` | Show chapter linking; `/continuity off` disables it |
| `/review` | Review your idea; `/review show` redisplays the saved review |
| `/accept` | Show suggestions; `/accept 1,3`, `/accept all`, `/accept <wording>`, or `/accept clear` |

### Session
| Command | What it does |
|---|---|
| `/export` | Export using the saved format; `/export fountain` overrides |
| `/clear` | Clear the transcript |
| `/quit` | End the session |

**Keys:** `Tab` completes a command · `Enter` sends · `Ctrl+N` writes next ·
`F1` help · `F2` plan · `F3` progress · `F4` export · `Ctrl+L` clear ·
`Ctrl+Q` quit.

Plain text sent at the prompt becomes a direction for the next unwritten
chapter. If there is no chapter plan yet, it is saved to the story notes.
"""
_COMMAND_WORDS = (
    "help",
    "write",
    "w",
    "guide",
    "g",
    "edit",
    "plan",
    "progress",
    "characters",
    "char",
    "prefs",
    "setup",
    "outline",
    "notes",
    "mode",
    "words",
    "continuity",
    "review",
    "accept",
    "export",
    "clear",
    "quit",
)


def _common_prefix(values: list[str]) -> str:
    if not values:
        return ""
    prefix = values[0]
    for value in values[1:]:
        limit = min(len(prefix), len(value))
        index = 0
        while index < limit and prefix[index] == value[index]:
            index += 1
        prefix = prefix[:index]
        if not prefix:
            break
    return prefix


class CommandInput(Input):
    """The prompt input, with Tab completing a command instead of moving focus."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("tab", "complete", "Complete", show=False)
    ]

    def action_complete(self) -> None:
        complete = getattr(self.app, "action_complete", None)
        if complete is not None:
            complete()


def _preferences_table(project: StoryProject) -> Table:
    meta = project.meta
    table = Table(box=None, pad_edge=False, show_header=False)
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
        ("Chapters", str(_chapter_goal(project))),
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
        table.add_row(
            Text(label),
            Text(value) if value else Text("not set", style="dim"),
        )
    return table


def _characters_table(project: StoryProject) -> Table:
    table = Table(box=None, pad_edge=False)
    table.add_column("#", justify="right", style="dim")
    table.add_column("Name", style="bold")
    table.add_column("Role")
    table.add_column("Description", style="dim")
    for number, character in enumerate(project.meta.characters, start=1):
        table.add_row(
            str(number),
            Text(character.name),
            Text(character.role) or Text("-", style="dim"),
            Text(character.description) or Text("-", style="dim"),
        )
    return table


def _fountain_preview(text: str) -> Text:
    """Colour a finished Fountain chapter for the transcript."""
    styled = Text()
    for line in text.splitlines():
        stripped = line.strip()
        if _SCENE_HEADING.match(stripped):
            style = "bold cyan"
        elif (
            stripped
            and stripped == stripped.upper()
            and any(char.isalpha() for char in stripped)
            and not stripped.endswith(":")
        ):
            style = "bold yellow"
        else:
            style = ""
        styled.append(line + "\n", style=style)
    return styled


def _outline_table(project: StoryProject) -> Table:
    written = {chapter.number: chapter for chapter in project.chapters()}
    table = Table(box=None, pad_edge=False)
    table.add_column("#", justify="right", style="dim")
    table.add_column("Chapter", style="bold")
    table.add_column("Beat", style="dim")
    table.add_column("Your direction")
    table.add_column("Target", justify="right")
    table.add_column("Status", justify="right")
    fallback = load_config().words_per_chapter
    for number, entry in enumerate(project.meta.outline, start=1):
        chapter = written.get(number)
        target = chapter_word_target(project, number, fallback=fallback)
        table.add_row(
            str(number),
            Text(entry.title),
            Text(entry.summary) or Text("-", style="dim"),
            Text(entry.direction) or Text("-", style="dim"),
            Text(f"{target:,}", style="bold" if entry.words else ""),
            (
                Text(f"{chapter.words:,} words", style="green")
                if chapter
                else Text("not written", style="dim")
            ),
        )
    return table


def _suggestions_table(review: IdeaReview) -> Table:
    table = Table(box=None, pad_edge=False)
    table.add_column("#", justify="right", style="dim")
    table.add_column("Optional suggestion")
    for number, suggestion in enumerate(review.suggestions, start=1):
        table.add_row(str(number), Text(suggestion))
    return table


class VersoApp(App):
    """The full-screen writing session."""

    TITLE = "Verso"
    SUB_TITLE = "interactive fiction"

    CSS = """
    Screen {
        layout: vertical;
        background: $surface;
    }
    #topbar {
        height: 3;
        background: $primary-darken-3;
        color: $text;
        padding: 0 1;
        content-align: left middle;
    }
    #transcript {
        height: 1fr;
        padding: 0 1;
        background: $surface;
        scrollbar-size-vertical: 1;
    }
    #stream-box {
        height: 40%;
        margin: 0 1;
        padding: 0 1;
        border: round $accent;
        background: $surface;
        display: none;
    }
    #stream-box.visible {
        display: block;
    }
    #stream {
        width: 1fr;
    }
    #prompt-row {
        height: 3;
        background: $panel;
        border-top: solid $primary;
    }
    #prompt-symbol {
        width: 3;
        content-align: center middle;
        color: $accent;
        text-style: bold;
    }
    #command {
        width: 1fr;
        height: 3;
        border: none;
        background: $panel;
        padding: 0 1;
    }
    #hint {
        height: 1;
        background: $panel;
        color: $text-muted;
        padding: 0 2;
    }
    #status {
        height: 1;
        background: $panel;
        color: $text-muted;
        padding: 0 1;
    }
    Footer {
        background: $panel;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("ctrl+q", "quit", "Quit"),
        Binding("ctrl+l", "clear", "Clear"),
        Binding("f1", "help", "Help"),
        Binding("ctrl+n", "write_next", "Write next"),
        Binding("f2", "plan", "Plan"),
        Binding("f3", "progress", "Progress"),
        Binding("f4", "export", "Export"),
    ]

    def __init__(self, project: StoryProject, llm: Provider) -> None:
        super().__init__()
        self.project = project
        self.llm = llm
        self.pending: tuple[object, ...] | None = None
        self._busy = False
        self._busy_label = ""
        self._spinner_index = 0
        self._spinner_timer = None
        self._stream_text = ""
        self._stream_sent = 0
        self._written = 0
        self._planned = 0
        self._words = 0
        self._scenes = 0

    # ------------------------------------------------------------------ layout
    def compose(self) -> ComposeResult:
        yield Static(id="topbar")
        yield RichLog(id="transcript", wrap=True, highlight=False, markup=True)
        with VerticalScroll(id="stream-box"):
            yield Static(id="stream")
        with Horizontal(id="prompt-row"):
            yield Static("❯", id="prompt-symbol")
            yield CommandInput(
                placeholder="Type /help for commands", id="command"
            )
        yield Static(id="hint")
        yield Static(id="status")
        yield Footer(compact=True, show_command_palette=False)

    def on_mount(self) -> None:
        meta = self.project.meta
        self.title = f"Verso — {meta.title}"
        self._refresh_chrome()
        self._update_hint()
        self._welcome()
        self.query_one("#command", Input).focus()

    # ----------------------------------------------------------------- chrome
    def _topbar_text(self) -> Text:
        meta = self.project.meta
        line = Text()
        line.append("VERSO", style="bold reverse")
        line.append(" · ", style="dim")
        line.append(meta.title, style="bold")
        badge = "SCREENPLAY" if self.project.is_screenplay else "PROSE"
        line.append(" · ", style="dim")
        line.append(badge, style="bold reverse")
        details = " · ".join(
            part for part in (meta.genre, meta.tone) if part
        )
        if details:
            line.append(" · ", style="dim")
            line.append(details, style="dim")
        line.append(" · ", style="dim")
        line.append(f"{self.llm.name}/{self.llm.model}", style="dim")
        return line

    def _progress_bar(self, width: int = 12) -> str:
        if not self._planned:
            return ""
        ratio = min(1.0, self._written / self._planned)
        filled = round(width * ratio)
        return "█" * filled + "░" * (width - filled)

    def _status_text(self) -> Text:
        progress = (
            f"{self._written}/{self._planned} chapters"
            if self._planned
            else f"{self._written} chapters"
        )
        bar = self._progress_bar()
        stats = f"{self._words:,} words"
        if self.project.is_screenplay:
            stats += f" · {self._scenes} scenes"
        if self._busy:
            frame = _SPINNER[self._spinner_index % len(_SPINNER)]
            return Text(
                f" {frame} {self._busy_label}   {bar} {progress} · {stats}",
                style="yellow",
            )
        return Text(
            f" {bar} {progress} · {stats} · {self.project.root} · Ctrl+Q quit",
            style="dim",
        )

    def _refresh_stats(self) -> None:
        chapters = self.project.chapters()
        self._written = len(chapters)
        self._planned = len(self.project.meta.outline)
        self._words = sum(chapter.words for chapter in chapters)
        self._scenes = sum(
            1
            for chapter in chapters
            for line in chapter.body.splitlines()
            if _SCENE_HEADING.match(line.strip())
        )

    def _refresh_chrome(self) -> None:
        self._refresh_stats()
        self.query_one("#topbar", Static).update(self._topbar_text())
        self.query_one("#status", Static).update(self._status_text())

    def _log(self, content: object, *, expand: bool = True) -> None:
        self.query_one("#transcript", RichLog).write(content, expand=expand)

    def _default_hint(self) -> Text:
        if self._busy:
            return Text(
                " The model is writing. New prompts unlock when it finishes.",
                style="dim",
            )
        if self.project.is_screenplay:
            tip = "Screenplay mode: chapters save as .fountain · /export writes Fountain"
        else:
            tip = "Prose mode: chapters save as .md · /export writes Markdown"
        return Text(
            f" Tab completes · Ctrl+N writes next · {tip}",
            style="dim",
        )

    def _update_hint(self, value: str = "") -> None:
        try:
            hint = self.query_one("#hint", Static)
        except NoMatches:
            # A queued Input.Changed can arrive while the app is shutting down.
            return
        if value.startswith("/"):
            token = value[1:].split(" ", 1)[0].lower()
            matches = [word for word in _COMMAND_WORDS if word.startswith(token)]
            if matches:
                hint.update(
                    Text(
                        "  " + "   ".join(f"/{word}" for word in matches[:8]),
                        style="dim",
                    )
                )
                return
        hint.update(self._default_hint())

    def _welcome(self) -> None:
        meta = self.project.meta
        summary = " · ".join(
            part
            for part in (
                meta.genre,
                meta.tone,
                MODE_LABELS[meta.mode],
                f"{meta.pov.value.replace('-', ' ')} POV",
                meta.tense,
            )
            if part
        )
        body = Text()
        body.append("Verso", style="bold cyan")
        body.append(" — a terminal co-writer for long-form fiction.\n", style="dim")
        if summary:
            body.append(summary + "\n", style="dim")
        if self.project.is_screenplay:
            body.append(
                "Screenplay mode: each chapter is written and saved as a "
                "Fountain script (.fountain).\n",
                style="dim",
            )
        body.append(
            "Type /help for commands. Plain text becomes a direction for "
            "the next chapter.",
            style="dim",
        )
        self._log(
            Panel(
                body,
                title=Text(meta.title),
                border_style="cyan",
                padding=(0, 2),
            ),
            expand=True,
        )
        if meta.outline:
            self._log(_outline_table(self.project))
        else:
            self._log(
                Text(
                    "No chapter plan yet. Type /outline to build one.",
                    style="yellow",
                )
            )
        if not self.project.chapters():
            self._log(
                Text(
                    "Nothing written yet. Type /write, or press Ctrl+N, to "
                    "start the first chapter.",
                    style="dim",
                )
            )

    # ----------------------------------------------------------------- input
    def _prompt_input(self, placeholder: str, value: str = "") -> None:
        widget = self.query_one("#command", Input)
        widget.placeholder = placeholder
        widget.value = value
        widget.select_all()
        widget.focus()

    def _reset_prompt(self) -> None:
        widget = self.query_one("#command", Input)
        widget.placeholder = "Type /help for commands"
        widget.value = ""
        widget.focus()
        self._update_hint()

    def on_input_changed(self, event: Input.Changed) -> None:
        self._update_hint(event.value)

    def action_complete(self) -> None:
        widget = self.query_one("#command", Input)
        value = widget.value
        if not value.startswith("/") or " " in value:
            return
        token = value[1:].lower()
        matches = sorted(
            {word for word in _COMMAND_WORDS if word.startswith(token)}
        )
        if not matches:
            return
        if len(matches) == 1:
            widget.value = f"/{matches[0]} "
        else:
            prefix = _common_prefix(matches)
            if len(prefix) > len(token):
                widget.value = f"/{prefix}"
        widget.cursor_position = len(widget.value)
        self._update_hint(widget.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        widget = self.query_one("#command", Input)
        widget.value = ""
        if self._busy:
            self._reset_prompt()
            self._log(
                Text("Still writing. Wait for the chapter to finish.", style="yellow")
            )
            return
        if self.pending is not None:
            self._handle_pending(value)
            self._refresh_chrome()
            return
        if not value:
            return
        if value.startswith("/"):
            self._handle_command(value)
        elif value in {str(number) for number in range(1, 10)}:
            self._handle_number(value)
        else:
            self._handle_free_text(value)
        self._refresh_chrome()

    # -------------------------------------------------------------- commands
    def _handle_command(self, text: str) -> None:
        raw = text[1:].strip()
        if not raw:
            self._show_help()
            return
        name, _, arg = raw.partition(" ")
        name = name.lower()
        arg = arg.strip()

        if name in {"help", "?"}:
            self._show_help()
        elif name in {"write", "w"}:
            self._cmd_write(arg)
        elif name in {"guide", "g", "hint", "direction"}:
            self._cmd_direction(arg)
        elif name in {"edit", "chapter"}:
            self._cmd_edit(arg)
        elif name in {"plan", "outline-plan"}:
            self._cmd_plan(arg)
        elif name in {"progress", "status", "show"}:
            self._cmd_progress(arg)
        elif name in {"characters", "char", "chars"}:
            self._cmd_characters(arg)
        elif name in {"prefs", "preferences"}:
            self._cmd_prefs(arg)
        elif name in {"setup", "wizard"}:
            self._cmd_setup(arg)
        elif name in {"outline", "regen", "regenerate"}:
            self._cmd_outline(arg)
        elif name in {"notes", "note"}:
            self._cmd_notes(arg)
        elif name in {"mode", "medium"}:
            self._cmd_mode(arg)
        elif name in {"words", "word", "length"}:
            self._cmd_words(arg)
        elif name in {"continuity", "link"}:
            self._cmd_continuity(arg)
        elif name in {"review", "feedback", "critique"}:
            self._cmd_review(arg)
        elif name in {"accept", "apply"}:
            self._cmd_accept(arg)
        elif name in {"export", "publish"}:
            self._cmd_export(arg)
        elif name in {"clear", "cls"}:
            self.action_clear()
        elif name in {"quit", "exit", "q"}:
            self.exit()
        else:
            suggestion = get_close_matches(name, _COMMAND_WORDS, n=1)
            hint = f" Did you mean /{suggestion[0]}?" if suggestion else ""
            self._log(
                Text(f"Unknown command: /{name}.{hint} Type /help.", style="red")
            )

    def _handle_number(self, value: str) -> None:
        routes = {
            "1": lambda: self._cmd_write(""),
            "2": lambda: self._log(
                Text("Use /write <number> to pick a chapter.", style="dim")
            ),
            "3": lambda: self._log(
                Text("Use /guide <number> to set a direction.", style="dim")
            ),
            "4": lambda: self._log(
                Text("Use /edit <number> to edit a chapter plan.", style="dim")
            ),
            "5": lambda: self._cmd_characters(""),
            "6": lambda: self._cmd_prefs(""),
            "7": lambda: self._cmd_outline(""),
            "8": lambda: self._cmd_export(""),
            "9": lambda: self._cmd_progress(""),
        }
        routes[value]()

    def _handle_free_text(self, text: str) -> None:
        outline = self.project.meta.outline
        if not outline:
            meta = self.project.meta
            meta.notes = f"{meta.notes}\n{text}".strip()
            self.project.save_meta()
            self._log(
                Text(
                    "Saved that to story notes. Type /outline to build a "
                    "chapter plan.",
                    style="green",
                )
            )
            return
        number = self._next_number()
        if number is None:
            self._log(
                Text(
                    "Every planned chapter is written. Use /write <n> to "
                    "rewrite one, or /outline to plan more.",
                    style="yellow",
                )
            )
            return
        entry = outline[number - 1]
        entry.direction = text
        self.project.save_meta()
        self._log(
            Text(
                f"Direction saved for chapter {number} — {entry.title}. "
                "Type /write to generate it.",
                style="green",
            )
        )

    # ------------------------------------------------------------------ write
    def _existing_chapter(self, number: int) -> Chapter | None:
        return next(
            (
                chapter
                for chapter in self.project.chapters()
                if chapter.number == number
            ),
            None,
        )

    def _next_number(self) -> int | None:
        outline = self.project.meta.outline
        if not outline:
            return None
        written = {chapter.number for chapter in self.project.chapters()}
        return next(
            (
                candidate
                for candidate in range(1, len(outline) + 1)
                if candidate not in written
            ),
            None,
        )

    def _cmd_write(self, arg: str) -> None:
        outline = self.project.meta.outline
        if not outline:
            self._log(
                Text(
                    "No chapter plan yet. Type /outline to build one first.",
                    style="yellow",
                )
            )
            return
        number = self._next_number()
        if arg:
            try:
                number = int(arg.split()[0])
            except ValueError:
                self._log(Text("Chapter number must be an integer.", style="red"))
                return
        if number is None:
            self._log(
                Text(
                    "Every planned chapter is written. Use /write <n> to "
                    "rewrite one, or /outline to plan more.",
                    style="yellow",
                )
            )
            return
        if not 1 <= number <= len(outline):
            self._log(
                Text(
                    f"Chapter {number} is outside the plan (1–{len(outline)}).",
                    style="red",
                )
            )
            return
        if self._existing_chapter(number) is not None:
            self.pending = ("overwrite", number)
            self._prompt_input(
                f"Chapter {number} exists. Overwrite? (y/N)", ""
            )
            return
        self._ask_direction(number, force=False)

    def _ask_direction(self, number: int, *, force: bool) -> None:
        entry = self.project.meta.outline[number - 1]
        self.pending = ("direction", number, force)
        self._log(Text(f"Chapter {number} — {entry.title}", style="bold"))
        self._log(
            Text(
                "What should happen in this chapter? Enter to skip.",
                style="dim",
            )
        )
        self._prompt_input(
            f"Direction for chapter {number} (Enter to skip)", entry.direction
        )

    def _cmd_direction(self, arg: str) -> None:
        outline = self.project.meta.outline
        if not outline:
            self._log(Text("No chapter plan yet. Type /outline first.", style="yellow"))
            return
        if not arg:
            self._log(Text("Usage: /guide <chapter> <direction>", style="dim"))
            return
        parts = arg.split(maxsplit=1)
        number = self._next_number()
        direction = arg
        if parts[0].isdigit():
            candidate = int(parts[0])
            if not 1 <= candidate <= len(outline):
                self._log(
                    Text(
                        f"Chapter {candidate} is outside the plan "
                        f"(1–{len(outline)}).",
                        style="red",
                    )
                )
                return
            number = candidate
            direction = parts[1] if len(parts) > 1 else ""
        if number is None:
            self._log(Text("Every chapter is written.", style="yellow"))
            return
        if not direction:
            self._log(Text("Give the direction after the chapter number.", style="dim"))
            return
        entry = outline[number - 1]
        entry.direction = "" if direction == "-" else direction
        self.project.save_meta()
        self._log(
            Text(
                f"Direction saved for chapter {number} — {entry.title}.",
                style="green",
            )
        )

    def _cmd_edit(self, arg: str) -> None:
        outline = self.project.meta.outline
        if not outline:
            self._log(Text("No chapter plan yet. Type /outline first.", style="yellow"))
            return
        if not arg:
            self._log(Text("Usage: /edit <chapter>", style="dim"))
            return
        try:
            number = int(arg.split()[0])
        except ValueError:
            self._log(Text("Chapter number must be an integer.", style="red"))
            return
        if not 1 <= number <= len(outline):
            self._log(
                Text(
                    f"Chapter {number} is outside the plan (1–{len(outline)}).",
                    style="red",
                )
            )
            return
        entry = outline[number - 1]
        self.pending = ("plan_title", number)
        self._prompt_input(f"Title for chapter {number}", entry.title)

    # ------------------------------------------------------------------ plan
    def _cmd_plan(self, arg: str) -> None:
        if not self.project.meta.outline:
            self._log(
                Text("No chapter plan yet. Type /outline to build one.", style="yellow")
            )
            return
        self._log(_outline_table(self.project))

    def _cmd_progress(self, arg: str) -> None:
        self._log(_preferences_table(self.project))
        if self.project.meta.characters:
            self._log(_characters_table(self.project))
        if self.project.meta.outline:
            self._log(_outline_table(self.project))
        else:
            self._log(
                Text("No chapter plan yet. Type /outline to build one.", style="yellow")
            )

    def _cmd_outline(self, arg: str) -> None:
        chapters = _chapter_goal(self.project)
        if arg:
            try:
                chapters = int(arg.split()[0])
            except ValueError:
                self._log(Text("Chapter count must be an integer.", style="red"))
                return
            if not 1 <= chapters <= 100:
                self._log(Text("Chapter count must be between 1 and 100.", style="red"))
                return
        if self.project.meta.outline:
            self.pending = ("regen_confirm", chapters)
            self._prompt_input(
                f"Replace the current plan with {chapters} chapters? (y/N)", ""
            )
            return
        self._start_outline(chapters)

    # ------------------------------------------------------------- characters
    def _cmd_characters(self, arg: str) -> None:
        action = arg.lower().strip()
        if action in {"manage", "edit"}:
            self._run_suspended(
                lambda: _manage_characters(self.project),
                "Opening character manager",
            )
            return
        if action in {"add", "new"}:
            self.pending = ("char_name",)
            self._prompt_input("Character name", "")
            return
        if not self.project.meta.characters:
            self._log(Text("No characters yet. Use /characters add.", style="dim"))
            return
        self._log(_characters_table(self.project))

    def _start_character_add(self, name: str) -> None:
        existing = {
            character.name.casefold() for character in self.project.meta.characters
        }
        if name.casefold() in existing:
            self._log(
                Text(f"{name} is already in the character list.", style="red")
            )
            return
        self.pending = ("char_role", name)
        self._prompt_input(f"Role for {name} (Enter to skip)", "")

    # ------------------------------------------------------------ preferences
    def _cmd_prefs(self, arg: str) -> None:
        action = arg.lower().strip()
        if action in {"edit", "change"}:
            self._run_suspended(
                lambda: _edit_preferences(self.project),
                "Opening story preferences",
            )
            return
        self._log(_preferences_table(self.project))

    def _cmd_setup(self, arg: str) -> None:
        self._run_suspended(
            lambda: _setup_story(self.project, self.llm),
            "Opening the setup wizard",
        )

    def _cmd_notes(self, arg: str) -> None:
        if arg:
            meta = self.project.meta
            meta.notes = f"{meta.notes}\n{arg}".strip()
            self.project.save_meta()
            self._log(Text("Story notes updated.", style="green"))
            return
        notes = self.project.meta.notes
        self._log(Text(notes or "No story notes yet.", style="dim" if not notes else ""))

    # ------------------------------------------------------------------ mode
    def _cmd_mode(self, arg: str) -> None:
        current = self.project.meta.mode
        value = arg.strip().lower()
        if not value:
            self._log(
                Text(
                    f"Writing mode: {MODE_LABELS[current]}. "
                    "Use /mode prose or /mode screenplay.",
                    style="dim",
                )
            )
            return
        aliases = {
            "script": "screenplay",
            "fountain": "screenplay",
            "novel": "prose",
        }
        value = aliases.get(value, value)
        if value not in {"prose", "screenplay"}:
            self._log(
                Text("Writing mode must be prose or screenplay.", style="red")
            )
            return
        mode = WritingMode(value)
        if mode == current:
            self._log(Text(f"Already in {MODE_LABELS[mode]}.", style="dim"))
            return
        self.project.meta.mode = mode
        formats = (
            ("fountain", "pdf")
            if mode == WritingMode.screenplay
            else ("md", "pdf")
        )
        if self.project.meta.export_format not in formats:
            self.project.meta.export_format = formats[0]
        self.project.save_meta()
        self._log(
            Text(
                f"Writing mode set to {MODE_LABELS[mode]}. New chapters save "
                f"as {self.project.chapter_suffix}; existing chapters are kept.",
                style="green",
            )
        )
        self.notify(
            f"New chapters will be saved as {self.project.chapter_suffix}",
            title="Writing mode changed",
        )
        self._refresh_chrome()
        self._update_hint()

    # --------------------------------------------------------------- words
    def _cmd_words(self, arg: str) -> None:
        meta = self.project.meta
        parts = arg.split()
        if not parts:
            resolved = meta.words_per_chapter or load_config().words_per_chapter
            source = "story override" if meta.words_per_chapter else "from config"
            self._log(
                Text(
                    f"Story target: ~{resolved:,} words per chapter ({source}). "
                    "Use /words 1200, or /words 3 900 for one chapter.",
                    style="dim",
                )
            )
            if meta.outline:
                self._log(_outline_table(self.project))
            return

        if len(parts) >= 2 and parts[0].isdigit():
            number = int(parts[0])
            if not meta.outline:
                self._log(
                    Text(
                        "No chapter plan yet. Type /outline first.",
                        style="yellow",
                    )
                )
                return
            if not 1 <= number <= len(meta.outline):
                self._log(
                    Text(
                        f"Chapter {number} is outside the plan "
                        f"(1–{len(meta.outline)}).",
                        style="red",
                    )
                )
                return
            entry = meta.outline[number - 1]
            if parts[1] == "-":
                entry.words = None
                self.project.save_meta()
                resolved = chapter_word_target(
                    self.project,
                    number,
                    fallback=load_config().words_per_chapter,
                )
                self._log(
                    Text(
                        f"Chapter {number} uses the story target "
                        f"(~{resolved:,} words).",
                        style="green",
                    )
                )
                self._refresh_chrome()
                return
            try:
                count = int(parts[1])
            except ValueError:
                self._log(Text("Word target must be an integer.", style="red"))
                return
            if count < 100:
                self._log(Text("Word target must be at least 100.", style="red"))
                return
            entry.words = count
            self.project.save_meta()
            self._log(
                Text(f"Chapter {number} now targets ~{count:,} words.", style="green")
            )
            self.notify(f"Chapter {number} → ~{count:,} words", title="Word target set")
            self._refresh_chrome()
            return

        try:
            count = int(parts[0])
        except ValueError:
            self._log(Text("Usage: /words [chapter] [count]", style="dim"))
            return
        if count < 100:
            self._log(Text("Word target must be at least 100.", style="red"))
            return
        meta.words_per_chapter = count
        self.project.save_meta()
        self._log(
            Text(
                f"Story target set to ~{count:,} words per chapter. "
                "Chapters with their own override keep it.",
                style="green",
            )
        )
        self.notify(f"Story target → ~{count:,} words", title="Word target set")
        self._refresh_chrome()

    # ---------------------------------------------------------- continuity
    def _cmd_continuity(self, arg: str) -> None:
        meta = self.project.meta
        value = arg.strip().lower()
        if not value:
            self._log(
                Text(
                    "Chapter continuity is "
                    f"{'on' if meta.continuity else 'off'}. "
                    "When on, each chapter prompt includes the previous "
                    "chapter's ending and asks the model to carry time, place, "
                    "characters, and threads forward. Use /continuity on or off.",
                    style="dim",
                )
            )
            return
        if value in {"on", "true", "yes", "1"}:
            meta.continuity = True
        elif value in {"off", "false", "no", "0"}:
            meta.continuity = False
        else:
            self._log(Text("Use /continuity on or /continuity off.", style="red"))
            return
        self.project.save_meta()
        self._log(
            Text(
                f"Chapter continuity {'on' if meta.continuity else 'off'}.",
                style="green",
            )
        )
        self.notify(
            f"Continuity {'enabled' if meta.continuity else 'disabled'}",
            title="Chapter linking",
        )
        self._refresh_chrome()

    # -------------------------------------------------------------- review
    def _cmd_review(self, arg: str) -> None:
        if arg.strip().lower() in {"show", "view"}:
            saved = self.project.meta.idea_review
            if not saved.reviewed_at:
                self._log(
                    Text(
                        "No idea review yet. Type /review to get one.",
                        style="yellow",
                    )
                )
                return
            self._log(
                Panel(
                    Text(saved.text),
                    title="Idea review — your idea is primary",
                    border_style="cyan",
                    padding=(0, 1),
                ),
                expand=True,
            )
            if saved.suggestions:
                self._log(_suggestions_table(saved))
            else:
                self._log(
                    Text("No optional suggestions were offered.", style="dim")
                )
            return
        if self._busy:
            self._when_idle(lambda: self._cmd_review(arg))
            return
        meta = self.project.meta
        premise = arg.strip() or meta.logline or meta.notes
        self._begin_busy("Reviewing your story idea")
        self._review_worker(premise)

    @work(thread=True, exclusive=True)
    def _review_worker(self, premise: str) -> None:
        try:
            review = review_idea(self.project, self.llm, premise=premise)
        except Exception as exc:
            self.call_from_thread(self._review_failed, str(exc))
            return
        self.call_from_thread(self._review_finished, review)

    def _review_finished(self, review: IdeaReview) -> None:
        self._end_busy()
        self._log(
            Panel(
                Text(review.text),
                title="Idea review — your idea is primary",
                border_style="cyan",
                padding=(0, 1),
            ),
            expand=True,
        )
        if review.suggestions:
            self._log(_suggestions_table(review))
            self._log(
                Text(
                    "Suggestions are advice only. Accept them with "
                    "/accept 1,3 or /accept all, enhance with /accept <your "
                    "wording>, or ignore them. Your idea stays primary.",
                    style="dim",
                )
            )
            self.notify(
                f"{len(review.suggestions)} optional suggestion(s)",
                title="Idea review ready",
            )
        else:
            self._log(
                Text(
                    "No optional suggestions. Your idea stands as written.",
                    style="dim",
                )
            )
            self.notify("Your idea stands as written", title="Idea review ready")
        self._reset_prompt()
        self._refresh_chrome()

    def _review_failed(self, message: str) -> None:
        self._end_busy()
        self._log(Text(f"Could not review the idea: {message}", style="red"))
        self.notify(message, title="Review failed", severity="error")
        self._reset_prompt()
        self._refresh_chrome()

    def _cmd_accept(self, arg: str) -> None:
        value = arg.strip()
        if value.lower() == "clear":
            removed = clear_accepted_suggestions(self.project)
            self._log(
                Text(
                    f"Removed {removed} accepted suggestion(s) from the story "
                    "bible."
                    if removed
                    else "No accepted suggestions to clear.",
                    style="green" if removed else "dim",
                )
            )
            self._refresh_chrome()
            return
        review = self.project.meta.idea_review
        if not review.suggestions:
            self._log(
                Text(
                    "No review suggestions yet. Type /review to get feedback "
                    "on your idea.",
                    style="yellow",
                )
            )
            return
        if not value:
            self._log(_suggestions_table(review))
            self._log(
                Text(
                    "Use /accept 1,3 or /accept all. Type your own wording "
                    "after /accept to enhance a suggestion. Nothing is applied "
                    "without this step.",
                    style="dim",
                )
            )
            return
        if value.lower() in {"all", "*"}:
            indexes = list(range(1, len(review.suggestions) + 1))
            enhancement = ""
        else:
            parts = [part for part in re.split(r"[,\s]+", value) if part]
            if parts and all(part.isdigit() for part in parts):
                indexes = [int(part) for part in parts]
                enhancement = ""
            else:
                indexes = []
                enhancement = value
        try:
            accepted = accept_review_suggestions(
                self.project, indexes, enhancement=enhancement
            )
        except GenerationError as exc:
            self._log(Text(str(exc), style="red"))
            return
        if accepted:
            self._log(
                Text(
                    f"Accepted {len(accepted)} item(s). They now guide later "
                    "chapters; your original idea is unchanged.",
                    style="green",
                )
            )
            self.notify(
                f"{len(accepted)} suggestion(s) accepted",
                title="Story bible updated",
            )
        else:
            self._log(Text("Nothing new to accept.", style="dim"))
        self._refresh_chrome()

    # ---------------------------------------------------------------- export
    def _cmd_export(self, arg: str) -> None:
        formats = (
            ("fountain", "pdf")
            if self.project.is_screenplay
            else ("md", "pdf")
        )
        default = (
            self.project.meta.export_format
            if self.project.meta.export_format in formats
            else formats[0]
        )
        fmt = arg.split()[0].lower() if arg else default
        aliases = {"markdown": "md", "fnt": "fountain", "txt": "fountain"}
        fmt = aliases.get(fmt, fmt)
        if fmt not in formats:
            self._log(
                Text(
                    f"Export format must be {' or '.join(formats)}.",
                    style="red",
                )
            )
            return
        self.project.meta.export_format = fmt
        self.project.save_meta()
        try:
            if fmt == "pdf":
                path = render_pdf(self.project)
            elif fmt == "fountain":
                path = write_fountain(self.project)
            else:
                path = write_markdown(self.project)
        except Exception as exc:
            self._log(Text(f"Export failed: {exc}", style="red"))
            self.notify("Export failed", title=str(exc), severity="error")
            return
        self._log(
            Text(
                f"Exported {path} ({path.stat().st_size:,} bytes).",
                style="green",
            )
        )
        self.notify(f"Saved {path.name}", title="Export complete")
        self._refresh_chrome()

    # ---------------------------------------------------------------- pending
    def _handle_pending(self, value: str) -> None:
        pending = self.pending
        self.pending = None
        if pending is None:
            return
        kind = pending[0]

        if kind == "overwrite":
            number = int(pending[1])
            if value.lower() in {"y", "yes"}:
                self._ask_direction(number, force=True)
            else:
                self._reset_prompt()
                self._log(Text("Cancelled.", style="dim"))
            return

        if kind == "direction":
            number = int(pending[1])
            force = bool(pending[2])
            entry = self.project.meta.outline[number - 1]
            if value == "-":
                entry.direction = ""
            elif value:
                entry.direction = value
            self.project.save_meta()
            self._reset_prompt()
            self._start_generation(number, force=force)
            return

        if kind == "regen_confirm":
            chapters = int(pending[1])
            if value.lower() in {"y", "yes"}:
                self._reset_prompt()
                self._start_outline(chapters)
            else:
                self._reset_prompt()
                self._log(Text("Cancelled.", style="dim"))
            return

        if kind == "plan_title":
            number = int(pending[1])
            entry = self.project.meta.outline[number - 1]
            entry.title = value or entry.title
            self.pending = ("plan_summary", number)
            self._prompt_input(f"Beat for chapter {number}", entry.summary)
            return

        if kind == "plan_summary":
            number = int(pending[1])
            entry = self.project.meta.outline[number - 1]
            entry.summary = value
            self.pending = ("plan_direction", number)
            self._prompt_input(f"Direction for chapter {number}", entry.direction)
            return

        if kind == "plan_direction":
            number = int(pending[1])
            entry = self.project.meta.outline[number - 1]
            entry.direction = "" if value == "-" else value
            self.project.save_meta()
            self._reset_prompt()
            self._log(Text("Chapter plan updated.", style="green"))
            self._log(_outline_table(self.project))
            return

        if kind == "char_name":
            if not value:
                self._reset_prompt()
                self._log(Text("No character added.", style="dim"))
                return
            self._start_character_add(value)
            return

        if kind == "char_role":
            name = str(pending[1])
            self.pending = ("char_description", name, value)
            self._prompt_input(f"Description for {name} (Enter to skip)", "")
            return

        if kind == "char_description":
            name = str(pending[1])
            role = str(pending[2])
            self.project.meta.characters.append(
                Character(name=name, role=role, description=value)
            )
            self.project.save_meta()
            self._reset_prompt()
            self._log(Text(f"Added {name}.", style="green"))
            return

        self._reset_prompt()

    # --------------------------------------------------------------- workers
    def _begin_busy(self, label: str) -> None:
        self._busy = True
        self._busy_label = label
        self._spinner_index = 0
        if self._spinner_timer is not None:
            self._spinner_timer.stop()
        self._spinner_timer = self.set_interval(0.12, self._tick_spinner)
        self._refresh_chrome()

    def _end_busy(self) -> None:
        self._busy = False
        self._busy_label = ""
        if self._spinner_timer is not None:
            self._spinner_timer.stop()
            self._spinner_timer = None
        self._refresh_chrome()

    def _tick_spinner(self) -> None:
        if not self._busy:
            return
        self._spinner_index += 1
        try:
            self.query_one("#status", Static).update(self._status_text())
        except NoMatches:
            return

    def _start_generation(self, number: int, *, force: bool) -> None:
        entry = self.project.meta.outline[number - 1]
        target = chapter_word_target(
            self.project, number, fallback=load_config().words_per_chapter
        )
        self._stream_text = ""
        self._stream_sent = 0
        self.query_one("#stream-box").add_class("visible")
        stream = self.query_one("#stream", Static)
        stream.border_title = f"Chapter {number} — {entry.title} (~{target:,} words)"
        stream.update("")
        self._begin_busy(
            f"Writing chapter {number} — {entry.title} (~{target:,} words)"
        )
        self._write_worker(number, force)

    @work(thread=True, exclusive=True)
    def _write_worker(self, number: int, force: bool) -> None:
        try:
            chapter = write_chapter(
                self.project,
                self.llm,
                number,
                words=chapter_word_target(
                    self.project,
                    number,
                    fallback=load_config().words_per_chapter,
                ),
                force=force,
                on_token=self._on_token,
            )
        except Exception as exc:  # surfaced in the UI, never silent
            self.call_from_thread(self._generation_failed, str(exc))
            return
        self.call_from_thread(self._generation_finished, chapter)

    def _on_token(self, token: str) -> None:
        self._stream_text += token
        if (
            len(self._stream_text) - self._stream_sent >= 120
            or token.endswith("\n")
        ):
            self._stream_sent = len(self._stream_text)
            self.call_from_thread(self._update_stream, self._stream_text)

    def _update_stream(self, text: str) -> None:
        self.query_one("#stream", Static).update(Text(text))
        self.query_one("#stream-box", VerticalScroll).scroll_end(animate=False)

    def _hide_stream(self) -> None:
        self.query_one("#stream-box").remove_class("visible")
        stream = self.query_one("#stream", Static)
        stream.border_title = ""
        stream.update("")

    def _generation_finished(self, chapter: Chapter) -> None:
        self._end_busy()
        self._hide_stream()
        if self.project.is_screenplay:
            self._log(_fountain_preview(chapter.body))
        else:
            self._log(Markdown(chapter.text))
        self._log(
            Text(
                f"Saved {chapter.path} · {chapter.words:,} words",
                style="green",
            )
        )
        self.notify(
            f"Chapter {chapter.number} saved · {chapter.words:,} words",
            title="Chapter written",
        )
        self._reset_prompt()
        self._refresh_chrome()

    def _generation_failed(self, message: str) -> None:
        self._end_busy()
        self._hide_stream()
        self._log(Text(f"Generation failed: {message}", style="red"))
        partial = list(self.project.chapters_dir.glob("*.part"))
        if partial:
            self._log(
                Text(
                    f"Partial text kept at {partial[0]}.",
                    style="yellow",
                )
            )
        self.notify(message, title="Generation failed", severity="error")
        self._reset_prompt()
        self._refresh_chrome()

    def _start_outline(self, chapters: int) -> None:
        self._begin_busy(f"Drafting a {chapters}-chapter plan")
        self._outline_worker(chapters)

    @work(thread=True, exclusive=True)
    def _outline_worker(self, chapters: int) -> None:
        try:
            entries = generate_outline(
                self.project,
                self.llm,
                chapters=chapters,
                premise=self.project.meta.logline,
            )
        except Exception as exc:
            self.call_from_thread(self._outline_failed, str(exc))
            return
        self.call_from_thread(self._outline_finished, entries)

    def _outline_finished(self, entries: list[OutlineEntry]) -> None:
        self._end_busy()
        self._log(
            Text(f"Saved {len(entries)} planned chapters.", style="green")
        )
        self._log(_outline_table(self.project))
        self.notify(
            f"{len(entries)} chapters planned", title="Outline ready"
        )
        self._reset_prompt()
        self._refresh_chrome()

    def _outline_failed(self, message: str) -> None:
        self._end_busy()
        self._log(Text(f"Could not generate the plan: {message}", style="red"))
        self.notify(message, title="Outline failed", severity="error")
        self._reset_prompt()

    # ------------------------------------------------------------------ help
    def _show_help(self) -> None:
        self._log(
            Panel(
                Markdown(_COMMANDS),
                title="Commands",
                border_style="cyan",
                padding=(0, 1),
            ),
            expand=True,
        )

    # --------------------------------------------------------------- actions
    def action_quit(self) -> None:
        self.exit()

    def action_clear(self) -> None:
        self.query_one("#transcript", RichLog).clear()
        self._welcome()

    def action_help(self) -> None:
        self._show_help()

    def _when_idle(self, action) -> None:
        if self._busy:
            self._log(
                Text("Still writing. Wait for the chapter to finish.", style="yellow")
            )
            self.notify(
                "Wait for the current generation to finish",
                title="Still writing",
                severity="warning",
            )
            return
        action()

    def action_write_next(self) -> None:
        self._when_idle(lambda: self._cmd_write(""))

    def action_plan(self) -> None:
        self._when_idle(lambda: self._cmd_plan(""))

    def action_progress(self) -> None:
        self._when_idle(lambda: self._cmd_progress(""))

    def action_export(self) -> None:
        self._when_idle(lambda: self._cmd_export(""))

    def _run_suspended(self, action, label: str) -> None:
        self._log(Text(f"{label}…", style="dim"))
        try:
            with self.suspend():
                action()
        except (EOFError, KeyboardInterrupt):
            self._log(Text("Wizard stopped; saved values were kept.", style="yellow"))
        except (StoryError, ProviderError, GenerationError) as exc:
            self._log(Text(f"Wizard failed: {exc}", style="red"))
            self.notify(str(exc), title="Wizard failed", severity="error")
        except Exception as exc:
            self._log(Text(f"Wizard failed: {exc}", style="red"))
            self.notify(str(exc), title="Wizard failed", severity="error")
        self._reset_prompt()
        self._refresh_chrome()
        self._log(Text("Back in the session. Type /plan to review.", style="dim"))


def launch_tui(project: StoryProject, llm: Provider) -> None:
    """Run the full-screen session and return when it exits."""
    VersoApp(project, llm).run()
