# Verso

A terminal co-writer for long-form fiction and screenplays. Plan a story,
generate chapters with an LLM, and export the result as Markdown, Fountain, or a
typeset PDF.

Stories have a **writing mode**. Prose stories keep every chapter as a plain
`.md` file; screenplay stories keep them as Fountain scripts (`.fountain`).
Either way the canonical text is a plain file on disk, so stories are
git-diffable, editor-friendly, and crash-safe. PDF is rendered from those files,
never stored as the source of truth.

## Install

Verso requires Python 3.11 or newer. Release wheels are pure Python and work on
macOS, Linux, and Windows. The distribution is named `verso-cli`; the command
is `verso`.

### macOS and Linux

Install the latest release:

```bash
curl -fsSL https://github.com/ark-7/verso/releases/latest/download/install.sh | bash
```

Pin a version:

```bash
curl -fsSL https://github.com/ark-7/verso/releases/download/v0.1.0/install.sh | bash -s -- --version v0.1.0
```

The installer downloads the wheel, verifies its SHA-256 checksum, and installs
it with `uv`, `pipx`, or `pip --user` (in that order). It never uses `sudo`.
Add `--with anthropic`, `--with openai`, or `--with all` to install provider
SDKs.

### Windows

Install the latest release from PowerShell:

```powershell
irm https://github.com/ark-7/verso/releases/latest/download/install.ps1 | iex
```

To pin a version, download the installer and run it:

```powershell
iwr https://github.com/ark-7/verso/releases/download/v0.1.0/install.ps1 -OutFile install.ps1
.\install.ps1 -Version v0.1.0
```

Add `-WithProviders` to install both provider SDKs.

### Manual install

Download the wheel and `SHA256SUMS` from the
[latest release](https://github.com/ark-7/verso/releases/latest), verify the
wheel's checksum with your platform's SHA-256 tool, then install it:

```bash
uv tool install verso_cli-0.1.0-py3-none-any.whl
# or: pipx install verso_cli-0.1.0-py3-none-any.whl
# or: python -m pip install --user verso_cli-0.1.0-py3-none-any.whl
```

### From a checkout (contributors)

```bash
git clone https://github.com/ark-7/verso.git
cd verso
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[all,dev]"        # or [anthropic] / [openai] only
```

Ollama support needs no extra SDK — it talks to the local HTTP API directly.
Release engineering is documented in [RELEASING.md](RELEASING.md).

## Quick start

The interactive route:

```bash
verso session                       # opens a story, or creates one if none exists
verso new "The Ferryman"            # in a terminal, creates and opens the session
verso new "The Ferryman" --no-guided  # create only

verso new "The Ferryman" --mode screenplay   # Fountain / screenplay mode
verso new "The Ferryman" -m screenplay       # same, short flag
```

The setup wizard asks for the writing mode too, and `/mode` or the
`verso mode` command can switch an existing story. Switching never deletes
chapters; new chapters use the selected mode's extension.

`verso session` asks for the story's tone, genre, point of view, opening,
intended ending, story notes, references, chapter count, words per chapter,
chapter continuity, export format, and characters. It saves every answer to
`story.json`, offers an advisory review of your idea, generates a chapter plan,
and then opens the full-screen session UI shown below.

The scripted route is unchanged:

```bash
verso new "The Ferryman" --no-guided \
    --author "A. Author" --genre "Literary noir" \
    --premise "A ferryman discovers the river is collecting debts she never owed."

verso outline --chapters 8          # generates story.json's chapter plan
verso words 1800                    # story-wide default for future chapters
verso words --chapter 3 2400        # one chapter's target
verso continuity on                 # link each chapter to the previous one
verso review                        # optional, advisory feedback on your idea
verso write                         # writes the next chapter, streaming to disk
verso write 3 --words 2000          # a specific chapter
verso write --no-continuity         # skip the previous-chapter context once
verso list                          # outline + which chapters are written
verso export --format md            # exports/the-ferryman.md
verso export --format fountain      # exports/the-ferryman.fountain
verso export --format pdf           # exports/the-ferryman.pdf
```

Every story is a directory:

```
the-ferryman/
├── story.json          # metadata, writing mode, characters, outline
├── chapters/
│   ├── 001-the-storm.md
│   └── 002-the-shore.md
└── exports/
    ├── the-ferryman.md
    └── the-ferryman.pdf
```

A screenplay project looks the same, with `.fountain` chapter files and a
Fountain export. Chapter files begin with a generated heading
(`# Chapter 1: The Storm`, a valid Fountain section in screenplay mode);
everything after it is prose or screenplay text. You can edit them by hand —
`verso export` picks up the changes immediately.

Exports default to the story's mode, so `verso export` on a screenplay writes
Fountain. The `--format` flag can request `md`, `fountain`, or `pdf` explicitly.

## Interactive sessions

`verso session` targets the current story (or the only nearby one), and creates
a new story if none exists. A new story gets a full setup wizard; an existing
story with an outline goes straight to the session. `--setup` forces the wizard
again, and `--story <path>` selects a specific project.

In a terminal, the session is a full-screen UI built with Textual: a persistent
header (story, mode, genre, tone, model), a scrollable transcript, a live
chapter preview while the model streams, a command line with tab completion and
inline suggestions, a hint line, a progress/status line, and a keybinding
footer. Successful writes, outlines, and exports raise a notification; errors
are surfaced both in the transcript and as an error toast. The setup wizard
runs in the plain terminal first, then hands over to the full-screen view.

```
❯ /help
```

| Key | Action |
|---|---|
| `Enter` | Send the current line |
| `Tab` | Complete a command name |
| `Ctrl+N` | Write the next chapter |
| `F1` | Help |
| `F2` | Show the chapter plan |
| `F3` | Show progress and preferences |
| `F4` | Export |
| `Ctrl+L` | Clear the transcript |
| `Ctrl+Q` | End the session |


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
| `/mode` | Show the writing mode; `/mode screenplay` or `/mode prose` switches |
| `/words` | Show word targets; `/words 1200` sets the story default |
| `/words 3 900` | Target ~900 words for chapter 3; `/words 3 -` clears it |
| `/continuity` | Show chapter linking; `/continuity off` disables it |
| `/review` | Review your idea; `/review show` redisplays the saved review |
| `/accept` | Show suggestions; `/accept 1,3`, `/accept all`, `/accept <wording>`, or `/accept clear` |
| `/export` | Export using the saved format; `/export fountain` or `/export pdf` overrides |
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

## Chapter length and continuity

Word targets resolve in this order: `verso write --words N` (one run), the
chapter's own override (`verso words --chapter 3 2400` or `/words 3 2400`), the
story default (`verso words 1800`), then `config.words_per_chapter`. The
interactive setup saves the story default, and `verso words` with no arguments
shows the resolved target for every planned chapter. Screenplays show the same
target as a page estimate (roughly 150 words per page).

Continuity is on by default. Every chapter prompt includes the previous
chapter's planned beat and its actual ending, and asks the model to keep the
same time, place, characters, and knowledge while advancing from that moment —
without recapping. Turn it off per run with `--no-continuity`, or persistently
with `verso continuity off` or `/continuity off`. Chapter 2+ still requires
chapter 1 to exist; continuity only controls what context is sent.

## Idea review

`verso review` (or `/review` in the session) asks the editor to look at the
idea as written. **Your idea is primary.** The reviewer must begin with what
works, ask questions rather than answer them, and offer numbered suggestions
that build on your premise instead of replacing it.

The review is saved to `story.json` as advice. Nothing reaches the story bible
until you explicitly accept it:

```bash
verso review                    # print feedback and offer suggestions
verso review --accept 1,3       # accept two saved suggestions
verso review --accept all       # accept every saved suggestion
verso review --accept "Mara hums when she is afraid."   # your own enhancement
verso review --clear            # remove every accepted suggestion
```

In the session, use `/accept 1,3`, `/accept all`, or `/accept <your wording>`.
Accepted items appear in the story bible as author-approved guidance; ignored
suggestions stay in the stored review. `verso outline --review` runs the review
before planning, and the setup wizard offers the same step.

## Screenplay mode

Set the mode at creation (`verso new ... --mode screenplay`), in the setup
wizard, with `verso mode screenplay`, or from the session with
`/mode screenplay`.

In screenplay mode Verso:

- writes chapters as `.fountain` files under `chapters/`;
- uses a screenwriter system prompt and asks for Fountain formatting — scene
  headings, action, uppercase character cues, parentheticals, dialogue, and
  transitions;
- plans each chapter as a sequence of 2–4 scenes;
- exports Fountain by default (`verso export`), with a title page generated
  from the story metadata;
- renders a Courier, industry-style screenplay when PDF is requested, using a
  small built-in Fountain parser (no extra dependency).

Chapter documents stay plain text and remain valid Fountain. The generated
`# Chapter N: Title` line is a Fountain section: editors use it for navigation,
and it does not appear in the rendered screenplay.

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
   ├── store.py      story.json + chapters/*.md|*.fountain, atomic writes
   ├── session.py    setup wizard + plain prompt-loop fallback
   ├── tui.py        full-screen Textual session UI
   ├── prompts.py    story bible, outline/chapter prompts, idea review prompt
   ├── generate.py   orchestration; streams tokens to a .part file, then renames
   ├── providers/    Anthropic (SDK), OpenAI (SDK), Ollama (httpx)
   └── export/       markdown.py, fountain.py (concatenate), pdf.py (ReportLab)
```

Design choices worth knowing:

- **Story bible context.** Every generation call sends a stable description of
  title, writing mode, genre, tone, POV, tense, characters, the requested
  opening and ending, references, per-chapter user directions, and the outline.
  The Anthropic backend marks it as a cached system block.
- **Two canonical formats, one project shape.** Prose chapters are Markdown and
  screenplay chapters are Fountain; both live in `chapters/` and both are read
  back by the same store. Switching mode keeps existing files and changes only
  the extension used for new chapters.
- **Continuity.** Each chapter prompt includes the previous chapter's planned
  beat and actual ending, plus an explicit instruction to carry time, place,
  characters, and unresolved threads forward without recapping. It is on by
  default and switchable per story or per run. Rolling summaries of all prior
  chapters are the planned next step.
- **Author-first review.** The editor prompt treats the author's premise as
  intent, never a draft to correct. Feedback is stored separately from the
  story bible; only suggestions the author explicitly accepts (or rewords) are
  added as guidance.
- **Resumable generation.** Tokens stream straight into `NNN-title.md.part`.
  Ctrl-C keeps the partial chapter (the prompt tells you so), and a failed call
  never leaves a half-written file under the final name.
- **PDF without dependencies.** ReportLab is pure Python, so there is no LaTeX,
  GTK, or headless Chromium to install. If you want better typography later,
  Typst or Pandoc can be added as optional export backends.

## Tests

```bash
ruff check src tests scripts
pytest
```

The suite covers project round-tripping in both writing modes, the interactive
setup and menu, the full-screen session UI through Textual's headless test
driver, outline parsing, prose and screenplay prompt contents, chapter word
target precedence, continuity context, idea-review parsing and explicit
acceptance, provider health checks, and the Markdown, Fountain, and PDF
exporters. It also checks that the installed distribution version matches
`src/verso/__init__.py`. Provider calls are not tested against live APIs.

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
- The built-in Fountain-to-PDF renderer supports the common spec subset
  (scene headings, action, character, parenthetical, dialogue, transitions,
  centered text). Exotic Fountain features such as dual dialogue, boneyard
  comments, and inline notes are ignored rather than typeset.
- Coherence degrades past the model's context strategy; the rolling-summary
  layer is the fix, not a bigger `max_tokens`.
