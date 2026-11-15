"""The `verso` command line interface."""

from __future__ import annotations

import json
import re
import sys
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import __version__
from .config import (
    PROVIDERS,
    Config,
    config_path,
    load_config,
    save_config,
)
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
from .health import check_providers
from .models import IdeaReview, WritingMode
from .providers import Provider, ProviderError, get_provider, provider_status
from .providers.ollama import (
    OllamaModel,
    list_cloud_models,
    list_local_models,
)
from .session import run_session
from .store import AmbiguousStoryError, StoryError, StoryProject, slugify

try:  # Windows consoles may default to a legacy code page.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):  # pragma: no cover
    pass

console = Console()
err_console = Console(stderr=True)

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="A terminal co-writer for long-form fiction.",
)
config_app = typer.Typer(no_args_is_help=True, help="Read and write user config.")
app.add_typer(config_app, name="config")


@contextmanager
def _errors():
    try:
        yield
    except (StoryError, ProviderError, GenerationError) as exc:
        err_console.print(f"[red]Error:[/red] {escape(str(exc))}")
        raise typer.Exit(code=1) from exc


def _project(story: Optional[Path]) -> StoryProject:
    if story is not None:
        return StoryProject.load(story)
    return StoryProject.resolve()


def _ask_story_title() -> str:
    while True:
        title = typer.prompt("Story title").strip()
        if title:
            return title
        console.print("[red]A title is required.[/red]")


def _writing_mode(value: str) -> WritingMode:
    normalized = value.strip().lower()
    aliases = {
        "novel": "prose",
        "script": "screenplay",
        "fountain": "screenplay",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in {"prose", "screenplay"}:
        err_console.print(
            "[red]Error:[/red] --mode must be prose or screenplay."
        )
        raise typer.Exit(code=2)
    return WritingMode(normalized)


def _print_review(review: IdeaReview) -> None:
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


def _parse_review_accept(answer: str, count: int) -> tuple[list[int], str]:
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


def _accept_prompt(project: StoryProject, review: IdeaReview) -> None:
    if not review.suggestions:
        return
    answer = typer.prompt(
        "Accept suggestions (e.g. 1,3 or 'all'; type your own wording to "
        "enhance; Enter keeps your idea unchanged)",
        default="",
    )
    indexes, enhancement = _parse_review_accept(answer, len(review.suggestions))
    accepted = accept_review_suggestions(
        project, indexes, enhancement=enhancement
    )
    if accepted:
        console.print(
            f"[green]Accepted[/green] {len(accepted)} item(s). They now guide "
            "later generations; your original idea is unchanged."
        )
    else:
        console.print("[dim]Your idea is unchanged.[/dim]")


def _session_project(
    story: Optional[Path],
    path: Optional[Path],
    title: str,
    mode: WritingMode = WritingMode.prose,
) -> StoryProject:
    """Find a story for `verso session`, creating one only when necessary."""
    if story is not None:
        return StoryProject.load(story)

    if path is not None:
        existing = StoryProject.find_here(path)
        if existing is not None:
            return existing
        new_title = title.strip() or _ask_story_title()
        project = StoryProject.create(path, title=new_title, mode=mode)
        console.print(f"[green]Created[/green] {escape(str(project.root))}")
        return project

    current = StoryProject.find_here()
    if current is not None:
        return current
    try:
        return StoryProject.resolve()
    except AmbiguousStoryError:
        raise  # several stories nearby: the user must pass --story
    except StoryError:
        pass

    new_title = title.strip() or _ask_story_title()
    root = Path.cwd() / slugify(new_title)
    project = StoryProject.create(root, title=new_title, mode=mode)
    console.print(f"[green]Created[/green] {escape(str(project.root))}")
    return project


def _is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _model_details(model: OllamaModel) -> str:
    if model.cloud:
        capabilities = [cap for cap in model.capabilities if cap != "completion"]
        return ", ".join(["cloud", *capabilities])
    details = []
    if model.parameter_size:
        details.append(model.parameter_size)
    if model.size:
        details.append(f"{model.size / 1e9:.1f} GB")
    return ", ".join(details)


def _choose_ollama_model(models: list[OllamaModel]) -> OllamaModel:
    if len(models) == 1:
        return models[0]

    table = Table(show_header=True, header_style="bold")
    table.add_column("#", justify="right")
    table.add_column("Model")
    table.add_column("Details", style="dim")
    for index, model in enumerate(models, start=1):
        table.add_row(str(index), model.name, _model_details(model))
    console.print(table)

    while True:
        answer = typer.prompt("Model number or name", default="1").strip()
        if answer.isdigit():
            index = int(answer)
            if 1 <= index <= len(models):
                return models[index - 1]
        for model in models:
            if answer in {model.name, model.name.split(":")[0]}:
                return model
        console.print("[red]Choose one of the listed models.[/red]")


def _prompt_ollama_model(config: Config) -> str:
    local = list_local_models(config.ollama_host)
    if local is None:
        raise ProviderError(
            f"Could not reach Ollama at {config.ollama_host}. "
            "Is `ollama serve` running?"
        )
    cloud = list_cloud_models()

    if not local and not cloud:
        raise ProviderError(
            "No Ollama models found. Pull one with `ollama pull <model>`, "
            "or run `ollama signin` to use cloud models."
        )

    interactive = _is_interactive()

    if local and cloud:
        if not interactive:
            # Local inference is free; never silently spend cloud credits.
            return local[0].name
        console.print(
            f"Ollama offers {len(local)} local model(s) and "
            f"{len(cloud)} cloud model(s)."
        )
        answer = ""
        while answer not in {"l", "local", "c", "cloud"}:
            answer = typer.prompt(
                "Use local or cloud models? [local/cloud]", default="local"
            ).strip().lower()
        models = local if answer in {"l", "local"} else cloud
    else:
        models = local or cloud
        if not local and not interactive:
            raise ProviderError(
                "No local Ollama models are installed. Pass `--model <name>` "
                "(for example `--model gemma4:cloud`) to choose a cloud model."
            )

    return _choose_ollama_model(models).name


def _provider(name: Optional[str], model: Optional[str]) -> Provider:
    # Provider and model always come from the flags or the user config. A
    # story does not pin them: a failed or experimental run used to do that,
    # which made later `verso config set provider ...` changes appear ignored.
    config = load_config()
    provider_name = (name or config.provider).strip().lower()
    if provider_name == "ollama" and not (
        model or config.ollama_model or config.model
    ):
        chosen = _prompt_ollama_model(config)
        config.ollama_model = chosen
        try:
            save_config(config)
        except OSError:
            # A read-only config dir shouldn't block this run; just don't persist.
            pass
        model = chosen
        if _is_interactive():
            console.print(f"[dim]Using Ollama model {escape(chosen)}.[/dim]")
    return get_provider(provider_name, model)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False, "--version", help="Show the version and exit.", is_eager=True
    ),
) -> None:
    if version:
        console.print(f"verso {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())
        raise typer.Exit()


@app.command()
def new(
    title: str = typer.Argument(..., help="Story title."),
    path: Optional[Path] = typer.Option(
        None, "--path", "-p", help="Directory to create. Defaults to the title."
    ),
    author: str = typer.Option("", "--author", "-a"),
    genre: str = typer.Option("", "--genre", "-g"),
    premise: str = typer.Option("", "--premise", help="One-line logline."),
    mode: str = typer.Option(
        "prose",
        "--mode",
        "-m",
        help="Writing mode: prose (Markdown) or screenplay (Fountain).",
    ),
    guided: bool = typer.Option(
        False,
        "--guided",
        "--interactive",
        "-i",
        help="Open the interactive setup after creating the story.",
    ),
    no_guided: bool = typer.Option(
        False,
        "--no-guided",
        "--no-interactive",
        help="Create the story only; do not open a session.",
    ),
    no_tui: bool = typer.Option(
        False,
        "--no-tui",
        help="Use the plain prompt loop instead of the full-screen session UI.",
    ),
    provider: Optional[str] = typer.Option(None, "--provider", help="LLM provider."),
    model: Optional[str] = typer.Option(None, "--model", help="Model id."),
) -> None:
    """Create a new story project in the current directory."""
    with _errors():
        root = path or (Path.cwd() / slugify(title))
        project = StoryProject.create(
            root,
            title=title,
            author=author,
            genre=genre,
            premise=premise,
            mode=_writing_mode(mode),
        )
        launch = not no_guided and (guided or _is_interactive())
        if launch and not _is_interactive():
            raise StoryError("--guided needs a terminal. Use --no-guided instead.")
        if launch:
            console.print(f"[green]Created[/green] {escape(str(project.root))}")
            llm = _provider(provider, model)
            run_session(project, llm, setup=True, tui=False if no_tui else None)
            return
    console.print(f"[green]Created[/green] {escape(str(project.root))}")
    console.print("Next: [bold]verso outline[/bold] to plan the chapters.")


@app.command()
def session(
    story: Optional[Path] = typer.Option(None, "--story", "-s", help="Story directory."),
    path: Optional[Path] = typer.Option(
        None, "--path", "-p", help="Directory for a new story."
    ),
    title: str = typer.Option(
        "", "--title", "-t", help="Title when creating a new story."
    ),
    mode: str = typer.Option(
        "prose",
        "--mode",
        "-m",
        help="Writing mode when creating a new story: prose or screenplay.",
    ),
    provider: Optional[str] = typer.Option(None, "--provider", help="LLM provider."),
    model: Optional[str] = typer.Option(None, "--model", help="Model id."),
    setup: bool = typer.Option(
        False, "--setup", help="Re-run the story setup wizard first."
    ),
    no_tui: bool = typer.Option(
        False,
        "--no-tui",
        help="Use the plain prompt loop instead of the full-screen session UI.",
    ),
) -> None:
    """Open an interactive writing session for a new or existing story."""
    with _errors():
        if not _is_interactive():
            raise StoryError(
                "The interactive session needs a terminal. For scripted use, "
                "run `verso new`, `verso outline`, and `verso write`."
            )
        project = _session_project(
            story, path, title, mode=_writing_mode(mode)
        )
        llm = _provider(provider, model)
        run_session(
            project,
            llm,
            setup=True if setup else None,
            tui=False if no_tui else None,
        )


@app.command("mode")
def mode_command(
    writing_mode: Optional[str] = typer.Argument(
        None, help="prose or screenplay. Omit to show the current mode."
    ),
    story: Optional[Path] = typer.Option(
        None, "--story", "-s", help="Story directory."
    ),
) -> None:
    """Show or change the story's writing mode.

    Screenplay mode writes chapters as Fountain scripts; prose mode writes
    Markdown chapters. Existing chapters are never deleted.
    """
    with _errors():
        project = _project(story)
        if writing_mode is None:
            console.print(
                f"[bold]{escape(project.meta.title)}[/bold] — "
                f"writing mode: [bold]{project.meta.mode.value}[/bold] "
                f"(chapters: {project.chapter_suffix})"
            )
            return
        selected = _writing_mode(writing_mode)
        project.meta.mode = selected
        if selected == WritingMode.screenplay:
            if project.meta.export_format not in {"fountain", "pdf"}:
                project.meta.export_format = "fountain"
        elif project.meta.export_format not in {"md", "pdf"}:
            project.meta.export_format = "md"
        project.save_meta()
    console.print(
        f"[green]Writing mode set to[/green] {escape(selected.value)}. "
        f"New chapters save as [bold]{project.chapter_suffix}[/bold]; "
        "existing chapters are kept."
    )


@app.command("words")
def words_command(
    count: Optional[int] = typer.Argument(
        None, help="Target words per chapter. Omit to show the current targets."
    ),
    chapter: Optional[int] = typer.Option(
        None, "--chapter", "-c", help="Set or clear one chapter's target."
    ),
    clear: bool = typer.Option(
        False, "--clear", help="Remove a story or chapter override."
    ),
    story: Optional[Path] = typer.Option(
        None, "--story", "-s", help="Story directory."
    ),
) -> None:
    """Show or set chapter word targets.

    A bare count sets the story default. Use --chapter to override one chapter.
    Precedence: --words on `verso write`, the chapter override, the story
    default, then config.words_per_chapter.
    """
    with _errors():
        project = _project(story)
        meta = project.meta
        fallback = load_config().words_per_chapter
        if count is None and not clear:
            resolved = meta.words_per_chapter or fallback
            source = "story override" if meta.words_per_chapter else "from config"
            console.print(
                f"Chapter target: [bold]{resolved:,} words[/bold] ({source})."
            )
            if meta.outline:
                table = Table(box=None, pad_edge=False)
                table.add_column("#", justify="right", style="dim")
                table.add_column("Chapter", style="bold")
                table.add_column("Target", justify="right")
                for number, entry in enumerate(meta.outline, start=1):
                    target = chapter_word_target(
                        project, number, fallback=fallback
                    )
                    table.add_row(
                        str(number),
                        escape(entry.title),
                        f"[bold]{target:,}[/bold]" if entry.words else f"{target:,}",
                    )
                console.print(table)
            return

        if clear:
            if chapter is None:
                meta.words_per_chapter = None
                project.save_meta()
                console.print(
                    f"[green]Cleared[/green] the story target; chapters use "
                    f"config.words_per_chapter ({fallback:,})."
                )
                return
            entry = _chapter_entry(project, chapter)
            entry.words = None
            project.save_meta()
            console.print(
                f"[green]Cleared[/green] chapter {chapter}'s target; it now "
                f"uses the story default."
            )
            return

        assert count is not None
        if count < 100:
            raise GenerationError("Word targets must be at least 100.")
        if chapter is None:
            meta.words_per_chapter = count
            project.save_meta()
            console.print(
                f"[green]Set[/green] the story target to ~{count:,} words per "
                "chapter. Chapters with their own override keep it."
            )
            return
        entry = _chapter_entry(project, chapter)
        entry.words = count
        project.save_meta()
        console.print(
            f"[green]Set[/green] chapter {chapter} ({escape(entry.title)}) to "
            f"~{count:,} words."
        )


def _chapter_entry(project: StoryProject, number: int):
    if not project.meta.outline:
        raise GenerationError("No outline yet. Run `verso outline` first.")
    if not 1 <= number <= len(project.meta.outline):
        raise GenerationError(
            f"Chapter {number} is outside the outline "
            f"(1–{len(project.meta.outline)})."
        )
    return project.meta.outline[number - 1]


@app.command("continuity")
def continuity_command(
    setting: Optional[str] = typer.Argument(
        None, help="on or off. Omit to show the current setting."
    ),
    story: Optional[Path] = typer.Option(
        None, "--story", "-s", help="Story directory."
    ),
) -> None:
    """Show or set whether chapters link to the previous chapter.

    With continuity on (the default), every chapter prompt includes the
    previous chapter's ending and its planned beat, and asks the model to carry
    time, place, characters, and unresolved threads forward.
    """
    with _errors():
        project = _project(story)
        meta = project.meta
        if setting is None:
            console.print(
                f"Chapter continuity: [bold]"
                f"{'on' if meta.continuity else 'off'}[/bold]."
            )
            return
        value = setting.strip().lower()
        if value in {"on", "true", "yes", "1"}:
            meta.continuity = True
        elif value in {"off", "false", "no", "0"}:
            meta.continuity = False
        else:
            err_console.print(
                "[red]Error:[/red] continuity must be on or off."
            )
            raise typer.Exit(code=2)
        project.save_meta()
    console.print(
        f"[green]Continuity {'enabled' if meta.continuity else 'disabled'}.[/green]"
    )


@app.command()
def outline(
    story: Optional[Path] = typer.Option(None, "--story", "-s", help="Story directory."),
    chapters: int = typer.Option(10, "--chapters", "-n", min=1, max=100),
    premise: str = typer.Option("", "--premise", help="Steer this outline."),
    review_first: bool = typer.Option(
        False,
        "--review",
        help="Review the story idea before planning (advice only).",
    ),
    provider: Optional[str] = typer.Option(None, "--provider", help="LLM provider."),
    model: Optional[str] = typer.Option(None, "--model", help="Model id."),
) -> None:
    """Generate (or regenerate) the chapter outline."""
    with _errors():
        project = _project(story)
        llm = _provider(provider, model)
        if review_first:
            with console.status("Reviewing your idea...", spinner="dots"):
                report = review_idea(
                    project,
                    llm,
                    premise=premise or project.meta.logline or project.meta.notes,
                )
            _print_review(report)
            if report.suggestions and _is_interactive():
                _accept_prompt(project, report)
            elif report.suggestions:
                console.print(
                    "[dim]Accept any suggestions with "
                    "`verso review --accept 1,3` before or after planning.[/dim]"
                )
        with console.status("Drafting the outline...", spinner="dots"):
            entries = generate_outline(
                project, llm, chapters=chapters, premise=premise
            )

    table = Table(box=None, pad_edge=False)
    table.add_column("#", justify="right", style="dim")
    table.add_column("Chapter", style="bold")
    table.add_column("Beat", style="dim")
    for number, entry in enumerate(entries, start=1):
        table.add_row(str(number), escape(entry.title), escape(entry.summary))
    console.print(table)
    console.print(
        f"[green]Saved[/green] {len(entries)} chapters to story.json. "
        "Next: [bold]verso write[/bold]"
    )


@app.command()
def write(
    chapter: Optional[int] = typer.Argument(
        None, help="Chapter number. Defaults to the next unwritten one."
    ),
    story: Optional[Path] = typer.Option(None, "--story", "-s", help="Story directory."),
    words: Optional[int] = typer.Option(None, "--words", "-w", min=100),
    continuity: Optional[bool] = typer.Option(
        None,
        "--continuity/--no-continuity",
        help=(
            "Include the previous chapter's ending and link the new chapter to "
            "it. Defaults to the story's continuity setting."
        ),
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing chapter."),
    provider: Optional[str] = typer.Option(None, "--provider", help="LLM provider."),
    model: Optional[str] = typer.Option(None, "--model", help="Model id."),
    stream: bool = typer.Option(True, "--stream/--no-stream"),
) -> None:
    """Write a chapter with the configured model, streaming it to disk."""
    with _errors():
        project = _project(story)
        number = chapter if chapter is not None else project.next_chapter_number()
        if not project.meta.outline:
            raise GenerationError("No outline yet. Run `verso outline` first.")
        if number < 1 or number > len(project.meta.outline):
            raise GenerationError(
                f"Chapter {number} is outside the outline "
                f"(1–{len(project.meta.outline)})."
            )
        entry = project.meta.outline[number - 1]
        llm = _provider(provider, model)
        target = chapter_word_target(
            project,
            number,
            explicit=words,
            fallback=load_config().words_per_chapter,
        )

        console.print(
            f"\n[bold]Chapter {number}: {escape(entry.title)}[/bold]"
        )
        console.print(
            f"[dim]Writing ~{target:,} words with {llm.name}/{llm.model}. "
            "Ctrl-C keeps the partial text.[/dim]\n"
        )

        def emit(token: str) -> None:
            if stream:
                sys.stdout.write(token)
                sys.stdout.flush()

        result = write_chapter(
            project,
            llm,
            number,
            words=target,
            force=force,
            continuity=continuity,
            on_token=emit,
        )
        if not stream:
            console.print(result.body, markup=False, highlight=False)

    console.print(
        f"\n[green]Saved[/green] {escape(str(result.path))} · "
        f"{result.words:,} words"
    )


@app.command()
def review(
    premise: str = typer.Option(
        "",
        "--premise",
        "-p",
        help="Review this idea text instead of the story's saved premise/notes.",
    ),
    accept: Optional[str] = typer.Option(
        None,
        "--accept",
        help="Accept saved suggestions by number (1,3) or 'all', without a new review.",
    ),
    clear: bool = typer.Option(
        False,
        "--clear",
        help="Remove all accepted suggestions from the story bible.",
    ),
    story: Optional[Path] = typer.Option(
        None, "--story", "-s", help="Story directory."
    ),
    provider: Optional[str] = typer.Option(None, "--provider", help="LLM provider."),
    model: Optional[str] = typer.Option(None, "--model", help="Model id."),
) -> None:
    """Review your story idea with the editor.

    The review is advisory and stored for later. Your idea is primary:
    suggestions enter the story bible only when you explicitly accept them,
    either at the prompt or with --accept.
    """
    with _errors():
        project = _project(story)
        if clear:
            removed = clear_accepted_suggestions(project)
            console.print(
                f"[green]Cleared[/green] {removed} accepted suggestion(s)."
                if removed
                else "[dim]No accepted suggestions to clear.[/dim]"
            )
            return
        if accept is not None:
            saved = project.meta.idea_review
            if not saved.reviewed_at:
                raise GenerationError(
                    "No idea review yet. Run `verso review` first."
                )
            _print_review(saved)
            indexes, enhancement = _parse_review_accept(
                accept, len(saved.suggestions)
            )
            accepted = accept_review_suggestions(
                project, indexes, enhancement=enhancement
            )
            if accepted:
                console.print(
                    f"[green]Accepted[/green] {len(accepted)} item(s). They "
                    "now guide later generations; your idea is unchanged."
                )
            else:
                console.print("[dim]Your idea is unchanged.[/dim]")
            return

        llm = _provider(provider, model)
        with console.status("Reviewing your idea...", spinner="dots"):
            report = review_idea(
                project,
                llm,
                premise=premise or project.meta.logline or project.meta.notes,
            )

    _print_review(report)
    if report.suggestions and _is_interactive():
        try:
            _accept_prompt(project, report)
        except (EOFError, KeyboardInterrupt):
            console.print(
                "\n[dim]Left unchanged. Suggestions are saved; accept them "
                "later with `verso review --accept N`.[/dim]"
            )
    elif report.suggestions:
        console.print(
            "[dim]Suggestions are saved. Accept them with "
            "`verso review --accept 1,3`; nothing is applied automatically.[/dim]"
        )


@app.command()
def export(
    story: Optional[Path] = typer.Option(None, "--story", "-s", help="Story directory."),
    fmt: Optional[str] = typer.Option(
        None,
        "--format",
        "-f",
        help="md, fountain, or pdf. Defaults to the story's writing mode.",
    ),
    out: Optional[Path] = typer.Option(None, "--out", "-o", help="Output path."),
    frontmatter: bool = typer.Option(
        False, "--frontmatter", help="Add YAML frontmatter (Markdown only)."
    ),
) -> None:
    """Export the story to Markdown, Fountain, or PDF."""
    with _errors():
        project = _project(story)
        chosen = (fmt or "").lower().strip()
        if not chosen:
            chosen = "fountain" if project.is_screenplay else "md"
        aliases = {
            "markdown": "md",
            "fnt": "fountain",
            "script": "fountain",
        }
        chosen = aliases.get(chosen, chosen)
        if chosen not in {"md", "fountain", "pdf"}:
            err_console.print(
                "[red]Error:[/red] --format must be md, fountain, or pdf."
            )
            raise typer.Exit(code=2)
        if chosen == "pdf":
            path = render_pdf(project, out)
        elif chosen == "fountain":
            path = write_fountain(project, out)
        else:
            path = write_markdown(project, out, frontmatter=frontmatter)
    console.print(
        f"[green]Exported[/green] {escape(str(path))} "
        f"({path.stat().st_size:,} bytes)"
    )


def _show_chapters(project: StoryProject) -> None:
    meta = project.meta
    console.print(
        f"[bold]{escape(meta.title)}[/bold] "
        f"[dim]— {escape(str(project.root))}[/dim]"
    )
    if not meta.outline:
        console.print("[dim]No outline yet. Run `verso outline`.[/dim]")
        return

    written = {chapter.number: chapter for chapter in project.chapters()}
    table = Table(box=None, pad_edge=False)
    table.add_column("#", justify="right", style="dim")
    table.add_column("Chapter", style="bold")
    table.add_column("Status", justify="right")
    for number, entry in enumerate(meta.outline, start=1):
        chapter = written.get(number)
        status = (
            f"[green]{chapter.words:,} words[/green]"
            if chapter
            else "[dim]not written[/dim]"
        )
        table.add_row(str(number), escape(entry.title), status)
    console.print(table)


def _show_story_overview(projects: list[StoryProject]) -> None:
    table = Table(box=None, pad_edge=False)
    table.add_column("Title", style="bold")
    table.add_column("Chapters", justify="right")
    table.add_column("Path", style="dim")
    for project in projects:
        written = len(project.chapter_paths())
        planned = len(project.meta.outline)
        chapters = f"{written}/{planned}" if planned else str(written)
        table.add_row(
            escape(project.meta.title),
            chapters,
            escape(str(project.root)),
        )
    console.print(table)
    console.print(
        "[dim]Multiple stories detected. Use --story <path> to target one.[/dim]"
    )


@app.command("list")
def list_chapters(
    story: Optional[Path] = typer.Option(None, "--story", "-s", help="Story directory."),
) -> None:
    """Show nearby stories, or the chapters of the current story."""
    with _errors():
        if story is not None:
            projects = [StoryProject.load(story)]
        else:
            current = StoryProject.find_here()
            if current is not None:
                projects = [current]
            else:
                projects = StoryProject.discover()
                if not projects:
                    raise StoryError(
                        "No stories found here or in nearby directories. "
                        "Run `verso new <title>` first."
                    )
    if len(projects) == 1:
        _show_chapters(projects[0])
    else:
        _show_story_overview(projects)


@app.command()
def healthcheck(
    provider: Optional[str] = typer.Option(
        None, "--provider", help="Check only this provider."
    ),
    probe: bool = typer.Option(
        False,
        "--probe",
        help=(
            "Send a minimal request to verify inference. Costs a few tokens "
            "and reveals rate-limit headers."
        ),
    ),
    as_json: bool = typer.Option(
        False, "--json", help="Print JSON instead of a table."
    ),
) -> None:
    """Check credentials, connectivity, models, and rate limits."""
    with _errors():
        config = load_config()
        results = check_providers(config, only=provider, probe=probe)

    if as_json:
        payload = [
            {**asdict(result), "usable": result.usable} for result in results
        ]
        typer.echo(json.dumps(payload, indent=2))
    else:
        table = Table(title="Provider health")
        table.add_column("Provider", style="bold")
        table.add_column("Status")
        table.add_column("Model")
        table.add_column("Details")
        for result in results:
            if result.status == "ready":
                status_style = "green"
            elif result.status == "degraded":
                status_style = "yellow"
            else:
                status_style = "red"
            details = result.detail
            if result.limits:
                limits = ", ".join(
                    f"{label} {value}"
                    for label, value in result.limits.items()
                )
                details = (
                    f"{details}; limits {limits}" if details else f"limits {limits}"
                )
            table.add_row(
                result.name,
                Text(result.status, style=status_style),
                result.model or "-",
                escape(details),
            )
        console.print(table)

        usable = [result for result in results if result.usable]
        active_name = (provider or config.provider).strip().lower()
        active = next(
            (result for result in results if result.name == active_name), None
        )
        console.print(
            f"\nUsable: [bold]{len(usable)}/{len(results)}[/bold] providers."
        )
        if active is not None:
            console.print(f"Active: [bold]{active.name}[/bold] ({active.status})")
            if not active.usable and usable:
                console.print(
                    "[yellow]Tip:[/yellow] switch with "
                    "`verso config set provider <name>`."
                )
        if not probe:
            console.print(
                "[dim]Use --probe to verify inference and read rate limits.[/dim]"
            )

    if provider is not None and not results[0].usable:
        raise typer.Exit(code=1)
    if not any(result.usable for result in results):
        raise typer.Exit(code=1)


@app.command("providers")
def providers_command() -> None:
    """List providers and whether their SDKs are installed."""
    table = Table(box=None)
    table.add_column("Provider", style="bold")
    table.add_column("Default model")
    table.add_column("SDK", justify="right")
    for name, model, installed in provider_status():
        status = "[green]installed[/green]" if installed else "[yellow]missing[/yellow]"
        table.add_row(name, model, status)
    console.print(table)
    console.print(
        "[dim]Install cloud SDKs with: pip install 'verso-cli\\[all]'[/dim]"
    )


@config_app.command("show")
def config_show() -> None:
    """Show the effective configuration."""
    config = load_config()
    console.print(f"[dim]{escape(str(config_path()))}[/dim]")
    table = Table(box=None)
    table.add_column("Key", style="bold")
    table.add_column("Value")
    for key, value in config.model_dump().items():
        if key.endswith("_api_key"):
            shown = "[green]set[/green]" if value else "[dim]not set[/dim]"
        elif value is None:
            shown = "[dim]default[/dim]"
        else:
            shown = escape(str(value))
        table.add_row(key, shown)
    console.print(table)
    console.print(
        "[dim]Env vars take precedence: ANTHROPIC_API_KEY, OPENAI_API_KEY[/dim]"
    )


@config_app.command("set")
def config_set(key: str, value: str) -> None:
    """Set a config value, e.g. `verso config set provider ollama`."""
    config = load_config()
    if key not in Config.model_fields:
        err_console.print(
            f"[red]Error:[/red] Unknown key {key!r}. "
            f"Choose from: {', '.join(Config.model_fields)}."
        )
        raise typer.Exit(code=2)

    if key == "provider":
        value = value.lower()
        if value not in PROVIDERS:
            err_console.print(
                f"[red]Error:[/red] provider must be one of: {', '.join(PROVIDERS)}."
            )
            raise typer.Exit(code=2)

    annotation = Config.model_fields[key].annotation
    if annotation is bool:
        parsed: object = value.lower() in {"1", "true", "yes", "on"}
    elif annotation is int:
        try:
            parsed = int(value)
        except ValueError:
            err_console.print(f"[red]Error:[/red] {key} must be an integer.")
            raise typer.Exit(code=2) from None
    else:
        parsed = value

    setattr(config, key, parsed)
    save_config(config)
    console.print(f"[green]Set[/green] {escape(key)} = {escape(str(parsed))}")


if __name__ == "__main__":  # pragma: no cover
    app()
