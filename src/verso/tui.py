"""Full-screen interactive session UI.

This is the closer-to-Claude-Code skin for the writing session: a persistent
header, a scrollable transcript, a live streaming pane, and a single command
line at the bottom. The plain prompt loop in :mod:`verso.session` remains the
fallback for non-terminal use and for ``--no-tui``.
"""

from __future__ import annotations

from typing import ClassVar

from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Input, RichLog, Static

from .config import load_config
from .export.markdown import write_markdown
from .export.pdf import render_pdf
from .generate import GenerationError, generate_outline, write_chapter
from .models import Character, OutlineEntry
from .providers.base import Provider, ProviderError
from .session import (
    _chapter_goal,
    _edit_preferences,
    _manage_characters,
    _setup_story,
)
from .store import Chapter, StoryError, StoryProject

_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_COMMANDS = """
| Command | What it does |
|---|---|
| `/write` or `/w` | Write the next unwritten chapter |
| `/write 3` | Write chapter 3 (asks about overwriting) |
| `/guide 3` | Set a direction for chapter 3 |
| `/edit 3` | Edit chapter 3's title, beat, and direction |
| `/plan` | Show the chapter plan |
| `/progress` | Show preferences, characters, and progress |
| `/characters` | Show characters; `/characters manage` to edit them |
| `/prefs` | Show preferences; `/prefs edit` to change them |
| `/setup` | Re-run the full story setup wizard |
| `/outline` | Regenerate the plan; `/outline 12` for 12 chapters |
| `/notes` | Show story notes; `/notes <text>` appends to them |
| `/export` | Export using the saved format; `/export pdf` overrides |
| `/clear` | Clear the transcript |
| `/quit` | End the session |

Plain text sent at the prompt becomes a direction for the next unwritten
chapter. If there is no chapter plan yet, it is saved to the story notes.
"""


def _preferences_table(project: StoryProject) -> Table:
    meta = project.meta
    table = Table(box=None, pad_edge=False, show_header=False)
    table.add_column("Preference", style="bold")
    table.add_column("Value")
    rows = [
        ("Author", meta.author),
        ("Genre", meta.genre),
        ("Tone", meta.tone),
        ("Point of view", meta.pov.value.replace("-", " ").capitalize()),
        ("Tense", meta.tense),
        ("Logline", meta.logline),
        ("Opening", meta.opening),
        ("Ending", meta.ending),
        ("References", "; ".join(meta.references)),
        ("Story notes", meta.notes),
        ("Chapters", str(_chapter_goal(project))),
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


def _outline_table(project: StoryProject) -> Table:
    written = {chapter.number: chapter for chapter in project.chapters()}
    table = Table(box=None, pad_edge=False)
    table.add_column("#", justify="right", style="dim")
    table.add_column("Chapter", style="bold")
    table.add_column("Beat", style="dim")
    table.add_column("Your direction")
    table.add_column("Status", justify="right")
    for number, entry in enumerate(project.meta.outline, start=1):
        chapter = written.get(number)
        table.add_row(
            str(number),
            Text(entry.title),
            Text(entry.summary) or Text("-", style="dim"),
            Text(entry.direction) or Text("-", style="dim"),
            (
                Text(f"{chapter.words:,} words", style="green")
                if chapter
                else Text("not written", style="dim")
            ),
        )
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
    #status {
        height: 1;
        background: $panel;
        color: $text-muted;
        padding: 0 1;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("ctrl+q", "quit", "Quit"),
        Binding("ctrl+l", "clear", "Clear"),
        Binding("f1", "help", "Help"),
        Binding("ctrl+n", "write_next", "Write next"),
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

    # ------------------------------------------------------------------ layout
    def compose(self) -> ComposeResult:
        yield Static(id="topbar")
        yield RichLog(id="transcript", wrap=True, highlight=False, markup=True)
        with VerticalScroll(id="stream-box"):
            yield Static(id="stream")
        with Horizontal(id="prompt-row"):
            yield Static("❯", id="prompt-symbol")
            yield Input(placeholder="Type /help for commands", id="command")
        yield Static(id="status")

    def on_mount(self) -> None:
        meta = self.project.meta
        self.title = f"Verso — {meta.title}"
        self._refresh_chrome()
        self._welcome()
        self.query_one("#command", Input).focus()

    # ----------------------------------------------------------------- chrome
    def _topbar_text(self) -> Text:
        meta = self.project.meta
        line = Text()
        line.append(" VERSO ", style="bold reverse")
        line.append("  ")
        line.append(meta.title, style="bold")
        details = " · ".join(
            part for part in (meta.genre, meta.tone) if part
        )
        if details:
            line.append(f"   {details}", style="dim")
        line.append(f"   {self.llm.name}/{self.llm.model}", style="dim")
        return line

    def _status_text(self) -> Text:
        written = len(self.project.chapters())
        planned = len(self.project.meta.outline)
        progress = f"{written}/{planned} chapters" if planned else f"{written} chapters"
        if self._busy:
            frame = _SPINNER[self._spinner_index % len(_SPINNER)]
            return Text(
                f" {frame} {self._busy_label}   {progress}   Ctrl+Q quit",
                style="yellow",
            )
        return Text(
            f" /help for commands   ·   {progress}   ·   "
            f"{self.project.root}   ·   Ctrl+Q quit",
            style="dim",
        )

    def _refresh_chrome(self) -> None:
        self.query_one("#topbar", Static).update(self._topbar_text())
        self.query_one("#status", Static).update(self._status_text())

    def _log(self, content: object, *, expand: bool = True) -> None:
        self.query_one("#transcript", RichLog).write(content, expand=expand)

    def _welcome(self) -> None:
        meta = self.project.meta
        summary = " · ".join(
            part
            for part in (
                meta.genre,
                meta.tone,
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
        elif name in {"export", "publish"}:
            self._cmd_export(arg)
        elif name in {"clear", "cls"}:
            self.action_clear()
        elif name in {"quit", "exit", "q"}:
            self.exit()
        else:
            self._log(
                Text(f"Unknown command: /{name}. Type /help.", style="red")
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

    # ---------------------------------------------------------------- export
    def _cmd_export(self, arg: str) -> None:
        default = (
            self.project.meta.export_format
            if self.project.meta.export_format in {"md", "pdf"}
            else "md"
        )
        fmt = arg.split()[0].lower() if arg else default
        if fmt == "markdown":
            fmt = "md"
        if fmt not in {"md", "pdf"}:
            self._log(Text("Export format must be md or pdf.", style="red"))
            return
        self.project.meta.export_format = fmt
        self.project.save_meta()
        try:
            path = (
                render_pdf(self.project)
                if fmt == "pdf"
                else write_markdown(self.project)
            )
        except Exception as exc:
            self._log(Text(f"Export failed: {exc}", style="red"))
            return
        self._log(
            Text(
                f"Exported {path} ({path.stat().st_size:,} bytes).",
                style="green",
            )
        )
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
        self.query_one("#status", Static).update(self._status_text())

    def _start_generation(self, number: int, *, force: bool) -> None:
        entry = self.project.meta.outline[number - 1]
        self._stream_text = ""
        self._stream_sent = 0
        self.query_one("#stream-box").add_class("visible")
        stream = self.query_one("#stream", Static)
        stream.border_title = f"Chapter {number} — {entry.title}"
        stream.update("")
        self._begin_busy(f"Writing chapter {number} — {entry.title}")
        self._write_worker(number, force)

    @work(thread=True, exclusive=True)
    def _write_worker(self, number: int, force: bool) -> None:
        try:
            chapter = write_chapter(
                self.project,
                self.llm,
                number,
                words=load_config().words_per_chapter,
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
        self._log(Markdown(chapter.text))
        self._log(
            Text(
                f"Saved {chapter.path} · {chapter.words:,} words",
                style="green",
            )
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
        self._reset_prompt()

    def _outline_failed(self, message: str) -> None:
        self._end_busy()
        self._log(Text(f"Could not generate the plan: {message}", style="red"))
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

    def action_write_next(self) -> None:
        if self._busy:
            self._log(Text("Still writing.", style="yellow"))
            return
        self._cmd_write("")

    def _run_suspended(self, action, label: str) -> None:
        self._log(Text(f"{label}…", style="dim"))
        try:
            with self.suspend():
                action()
        except (EOFError, KeyboardInterrupt):
            self._log(Text("Wizard stopped; saved values were kept.", style="yellow"))
        except (StoryError, ProviderError, GenerationError) as exc:
            self._log(Text(f"Wizard failed: {exc}", style="red"))
        except Exception as exc:
            self._log(Text(f"Wizard failed: {exc}", style="red"))
        self._refresh_chrome()
        self._log(Text("Back in the session. Type /plan to review.", style="dim"))


def launch_tui(project: StoryProject, llm: Provider) -> None:
    """Run the full-screen session and return when it exits."""
    VersoApp(project, llm).run()
