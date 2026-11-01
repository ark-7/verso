# Verso

A terminal co-writer for long-form fiction. Plan a story, generate chapters with
an LLM, and export the result as Markdown or a typeset PDF.

Markdown is the canonical format: every chapter is a plain `.md` file on disk,
so stories are git-diffable, editor-friendly, and crash-safe. PDF is rendered
from those files, never stored as the source of truth.

## Install

The PyPI distribution is `verso-cli`; the command it installs is still `verso`.
Until it is published, install from a checkout:

```bash
python -m venv .venv
source .venv/Scripts/activate      # Windows: .venv\Scripts\activate
pip install -e ".[all,dev]"        # or [anthropic] / [openai] only
```

A pinned runtime lock is also available:

```bash
pip install -r requirements.txt    # installs the package and both provider SDKs
```

Ollama support needs no extra SDK — it talks to the local HTTP API directly.

## Quick start

The interactive route:

```bash
verso session                       # opens a story, or creates one if none exists
verso new "The Ferryman"            # in a terminal, creates and opens the session
verso new "The Ferryman" --no-guided  # create only
```

`verso session` asks for the story's tone, genre, point of view, opening,
intended ending, story notes, references, chapter count, export format, and
characters. It saves every answer to `story.json`, generates a chapter plan,
and then opens the full-screen session UI shown below.

The scripted route is unchanged:

```bash
verso new "The Ferryman" --no-guided \
    --author "A. Author" --genre "Literary noir" \
    --premise "A ferryman discovers the river is collecting debts she never owed."

verso outline --chapters 8          # generates story.json's chapter plan
verso write                         # writes the next chapter, streaming to disk
verso write 3 --words 2000          # a specific chapter
verso list                          # outline + which chapters are written
verso export --format md            # exports/the-ferryman.md
verso export --format pdf           # exports/the-ferryman.pdf
```

Every story is a directory:

```
the-ferryman/
├── story.json          # metadata, characters, outline
├── chapters/
│   ├── 001-the-storm.md
│   └── 002-the-shore.md
└── exports/
    ├── the-ferryman.md
    └── the-ferryman.pdf
```

Chapter files begin with a generated H1 (`# Chapter 1: The Storm`); everything
after it is pure prose. You can edit them by hand — `verso export` picks up the
changes immediately.

## Interactive sessions

`verso session` targets the current story (or the only nearby one), and creates
a new story if none exists. A new story gets a full setup wizard; an existing
story with an outline goes straight to the session. `--setup` forces the wizard
again, and `--story <path>` selects a specific project.

In a terminal, the session is a full-screen UI built with Textual: a persistent
header (story, genre, tone, model), a scrollable transcript, a live chapter
preview while the model streams, a command line at the bottom, and a status
line. The setup wizard runs in the plain terminal first, then hands over to the
full-screen view.

```
❯ /help
```

| Command | What it does |
|---|---|
| `/write` or `/w` | Write the next unwritten chapter |
| `/write 3` | Write chapter 3, asking before overwriting |
| `/guide 3` | Set or clear a direction for chapter 3 |
| `/edit 3` | Edit chapter 3's title, beat, and direction |
| `/plan` | Show the chapter plan and which chapters are written |
| `/progress` | Show preferences, characters, and progress |
| `/characters` | Show characters; `/characters add` or `manage` to edit |
| `/prefs` | Show preferences; `/prefs edit` to change them |
| `/setup` | Re-run the full setup wizard |
| `/outline` | Regenerate the plan; `/outline 12` for 12 chapters |
| `/notes` | Show story notes; `/notes <text>` appends to them |
| `/export` | Export using the saved format; `/export pdf` overrides |
| `/clear`, `/quit` | Clear the transcript or end the session |

Plain text sent at the prompt is treated as a direction for the next unwritten
chapter. If the story has no plan yet, it is saved to the story notes instead.
A direction is stored per chapter and sent to the model as a constraint that
takes precedence over the broader outline beat. Enter `-` at a direction
prompt to clear one. Everything is written to `story.json` as you go, so
ending the session never loses work.

`verso new` opens the session automatically when it is run in a terminal. Use
`--no-guided` (or `--no-interactive`) for scripts, CI, or when you only want to
create the directory. Piped or redirected runs never launch the session. Add
`--no-tui` to `new` or `session` to use the plain prompt loop instead; it is
also the automatic fallback when Textual is not installed. That loop offers the
same actions through a numbered menu.

**Story auto-detection.** Commands look for `story.json` in the current
directory and its ancestors. If there is none, Verso scans up to two levels of
subdirectories and uses the only story it finds. With several present it lists
the candidates and asks for `--story <path>`. Running `verso list` from a
workspace shows every detected story with its progress.

## Configuration

```bash
verso config set provider anthropic     # anthropic | openai | ollama
verso config set model claude-opus-5-5
verso config set words_per_chapter 1800
verso config set ollama_host http://127.0.0.1:11434
verso config set ollama_model gemma4:cloud
verso config show
```

Provider and model are global settings: changing them applies to every story
immediately. Use the per-command `--provider` / `--model` flags for one-off
overrides.

On the first Ollama run with no model set, Verso searches the local daemon and
the Ollama Cloud catalog, then asks whether to use local or cloud models and
which one. The choice is saved as `ollama_model`, kept separate from the generic
`model` so it never leaks into the Anthropic or OpenAI providers.

Cloud models use a `:cloud` suffix (for example `gemma4:cloud`) and are proxied
by your local Ollama daemon. Run `ollama signin` once to enable them; the model
catalog itself is public. `--model gemma4:cloud` skips the prompt. Setting a
local model that is not installed produces an error listing the available ones.

Config lives at `%APPDATA%\verso\config.toml` on Windows and
`~/.config/verso/config.toml` elsewhere (override with `VERSO_CONFIG_DIR`).
Keys are best supplied via environment variables, which take precedence:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export OPENAI_API_KEY=sk-...
```

`verso providers` shows which SDKs are installed. Per-command overrides:
`--provider`, `--model`, `--story <dir>`.

## Health checks

```bash
verso healthcheck                      # free readiness checks
verso healthcheck --probe              # + a minimal request per provider
verso healthcheck --provider ollama    # one provider only
verso healthcheck --json               # machine-readable
```

The default pass verifies installed SDKs, credentials, connectivity, and
whether the configured model is listed. For Ollama it reports the server
version and the local/cloud model counts. `--probe` sends a tiny generation
request (a few tokens may be charged), confirms inference actually works, and
prints latency plus the rate-limit headers returned by the provider.

Statuses are `ready`, `degraded` (reachable but the configured model is not
listed, or cloud sign-in has not been verified), and failure states such as
`no credentials`, `unreachable`, or `error`. The command exits non-zero when no
usable provider is found, or when the provider selected with `--provider` is
unusable.

Anthropic credentials can come from `ANTHROPIC_API_KEY`, an `ant auth login`
profile, or `verso config set anthropic_api_key <key>` — in that order. If none
is available, Verso says so instead of surfacing the SDK's raw error.

## How it works

```
CLI (typer + rich)
   │
   ├── store.py      story.json + chapters/*.md, atomic writes
   ├── session.py    setup wizard + plain prompt-loop fallback
   ├── tui.py        full-screen Textual session UI
   ├── prompts.py    story bible, outline prompt, chapter prompt
   ├── generate.py   orchestration; streams tokens to a .part file, then renames
   ├── providers/    Anthropic (SDK), OpenAI (SDK), Ollama (httpx)
   └── export/       markdown.py (concatenate), pdf.py (ReportLab)
```

Design choices worth knowing:

- **Story bible context.** Every generation call sends a stable description of
  title, genre, tone, POV, tense, characters, the requested opening and ending,
  references, per-chapter user directions, and the outline. The Anthropic
  backend marks it as a cached system block.
- **Continuity.** Each chapter prompt includes the ending of the previous
  chapter. This is the first version of the "memory" layer; rolling summaries of
  all prior chapters are the planned next step.
- **Resumable generation.** Tokens stream straight into `NNN-title.md.part`.
  Ctrl-C keeps the partial chapter (the prompt tells you so), and a failed call
  never leaves a half-written file under the final name.
- **PDF without dependencies.** ReportLab is pure Python, so there is no LaTeX,
  GTK, or headless Chromium to install. If you want better typography later,
  Typst or Pandoc can be added as optional export backends.

## Tests

```bash
pytest
```

The suite covers project round-tripping, the interactive setup and menu, the
full-screen session UI through Textual's headless test driver, outline parsing,
prompt contents, provider health checks, and both exporters. Provider calls are
not tested against live APIs.

## Roadmap

- [ ] Rolling chapter summaries for long-novel continuity
- [ ] Standalone `verso character` commands (editing is available in `verso session`)
- [ ] Rewrite / continue / expand commands on an existing chapter
- [ ] Optional Typst or Pandoc PDF backend
- [ ] Table of contents and page-numbered front matter in PDF
- [ ] Prompt/eval harness for comparing models on the same outline

## Known limitations

- PDF uses ReportLab's built-in fonts (WinAnsi), which cover Latin scripts.
  Non-Latin storytelling needs an embedded TTF (e.g. DejaVu) — a small change
  in `export/pdf.py`.
- Coherence degrades past the model's context strategy; the rolling-summary
  layer is the fix, not a bigger `max_tokens`.
