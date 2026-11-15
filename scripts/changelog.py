#!/usr/bin/env python3
"""Generate release notes and keep CHANGELOG.md current.

Notes are built from the commits between the previous release tag and the tag
being released. Conventional Commit prefixes are grouped into sections; every
other commit lands under "Other Changes". The same text feeds the GitHub
Release description and the changelog file, so the two never drift.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

CHANGELOG_HEADER = """# Changelog

All notable changes to Verso are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).
"""

SECTIONS: tuple[tuple[str, set[str] | None], ...] = (
    ("Breaking Changes", None),
    ("Added", {"feat"}),
    ("Fixed", {"fix"}),
    ("Performance", {"perf"}),
    ("Changed", {"refactor"}),
    ("Documentation", {"docs"}),
    ("Build", {"build"}),
    ("CI", {"ci"}),
    ("Tests", {"test"}),
    ("Style", {"style"}),
    ("Chores", {"chore"}),
    ("Reverted", {"revert"}),
    ("Other Changes", None),
)
TYPE_TO_SECTION = {
    kind: label for label, kinds in SECTIONS if kinds is not None for kind in kinds
}

CONVENTIONAL = re.compile(
    r"^(?P<type>[A-Za-z]+)(?:\((?P<scope>[^)]+)\))?(?P<breaking>!)?:\s*(?P<title>.+)$"
)
NOISE = re.compile(r"^(?:chore\(release\)|docs\(changelog\)|release\s+v?\d)", re.IGNORECASE)
BREAKING_MARKER = re.compile(r"^BREAKING[ -]CHANGE:", re.MULTILINE)
UNRELEASED = re.compile(r"^##\s+\[?Unreleased\]?", re.IGNORECASE)


@dataclass
class Commit:
    sha: str
    short: str
    title: str
    scope: str | None
    kind: str | None
    breaking: bool


def git(*args: str) -> str:
    result = subprocess.run(["git", *args], check=True, text=True, capture_output=True)
    return result.stdout


def resolve(ref: str) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"error: git ref not found: {ref}")
    return result.stdout.strip()


def previous_tag(target: str) -> str | None:
    result = subprocess.run(
        ["git", "describe", "--tags", "--abbrev=0", f"{target}^"],
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def release_date(target: str) -> str:
    stamp = git("log", "-1", "--format=%cs", target).strip()
    return stamp or date.today().isoformat()


def detect_repo() -> str | None:
    result = subprocess.run(
        ["git", "remote", "get-url", "origin"], text=True, capture_output=True
    )
    if result.returncode != 0:
        return None
    match = re.search(r"github\.com[:/](?P<repo>[^/]+/[^/]+?)(?:\.git)?$", result.stdout.strip())
    return match.group("repo") if match else None


def collect(previous: str | None, target: str) -> list[Commit]:
    revision = f"{previous}..{target}" if previous else target
    raw = git(
        "log",
        "--no-merges",
        "--reverse",
        "--format=%H%x1f%h%x1f%s%x1f%b%x1e",
        revision,
        "--",
        ".",
        ":(exclude)CHANGELOG.md",
    )
    commits: list[Commit] = []
    for record in raw.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        fields = record.split("\x1f")
        if len(fields) < 4:
            continue
        sha, short, subject, body = fields[0], fields[1], fields[2], fields[3]
        if NOISE.match(subject):
            continue
        match = CONVENTIONAL.match(subject)
        if match is None:
            title, scope, kind = subject.strip(), None, None
        else:
            title = match.group("title").strip()
            scope = match.group("scope")
            kind = match.group("type").lower()
        breaking = bool(BREAKING_MARKER.search(body)) or bool(match and match.group("breaking"))
        commits.append(Commit(sha, short, title, scope, kind, breaking))
    return commits


def render_commit(commit: Commit, repo: str | None) -> str:
    title = commit.title
    if title[:1].isascii() and title[:1].islower():
        title = title[0].upper() + title[1:]
    if commit.scope:
        title = f"**{commit.scope}**: {title}"
    if repo:
        title += f" ([{commit.short}](https://github.com/{repo}/commit/{commit.sha}))"
    else:
        title += f" ({commit.short})"
    return f"- {title}"


def render_body(commits: list[Commit], repo: str | None) -> str:
    grouped: dict[str, list[Commit]] = {label: [] for label, _ in SECTIONS}
    for commit in commits:
        if commit.breaking:
            grouped["Breaking Changes"].append(commit)
        else:
            grouped[TYPE_TO_SECTION.get(commit.kind or "", "Other Changes")].append(commit)

    lines: list[str] = []
    for label, _ in SECTIONS:
        if grouped[label]:
            lines.append(f"### {label}")
            lines.extend(render_commit(commit, repo) for commit in grouped[label])
            lines.append("")
    if not lines:
        lines = ["No notable changes.", ""]
    return "\n".join(lines).rstrip("\n")


def changelog_link(repo: str | None, previous: str | None, tag: str) -> str | None:
    if not repo:
        return None
    if previous:
        url = f"https://github.com/{repo}/compare/{previous}...{tag}"
    else:
        url = f"https://github.com/{repo}/commits/{tag}"
    return f"**Full Changelog**: {url}"


def render_notes(
    commits: list[Commit], repo: str | None, previous: str | None, tag: str
) -> str:
    body = render_body(commits, repo)
    link = changelog_link(repo, previous, tag)
    return f"{body}\n\n{link}\n" if link else f"{body}\n"


def render_entry(
    commits: list[Commit],
    repo: str | None,
    previous: str | None,
    tag: str,
    released: str,
) -> str:
    version = tag[1:] if tag.startswith("v") else tag
    label = f"[{version}]"
    if repo:
        label = f"[{version}](https://github.com/{repo}/releases/tag/{tag})"

    parts = [f"## {label} - {released}", "", render_body(commits, repo)]
    link = changelog_link(repo, previous, tag)
    if link:
        parts += ["", link]
    return "\n".join(parts).rstrip("\n") + "\n"


def _splice(before: list[str], entry: list[str], after: list[str]) -> str:
    head = list(before)
    while head and not head[-1].strip():
        head.pop()
    tail = list(after)
    while tail and not tail[0].strip():
        tail.pop(0)

    parts = head + ([""] if head else []) + entry
    if tail:
        parts += ["", *tail]
    text = "\n".join(parts).rstrip("\n") + "\n"
    text = re.sub(r"\n{3,}", "\n\n", text)
    if not text.startswith("# Changelog"):
        text = CHANGELOG_HEADER + "\n" + text
    return text


def update_changelog(path: Path, entry: str, version: str) -> bool:
    original = path.read_text(encoding="utf-8") if path.exists() else CHANGELOG_HEADER
    lines = original.splitlines()
    entry_lines = entry.rstrip("\n").splitlines()
    heading = re.compile(
        rf"^##\s+(?:\[{re.escape(version)}\]|{re.escape(version)}(?:\s|$))"
    )

    start = next((i for i, line in enumerate(lines) if heading.match(line)), None)
    if start is not None:
        end = next(
            (i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")),
            len(lines),
        )
        text = _splice(lines[:start], entry_lines, lines[end:])
    else:
        unreleased = next(
            (i for i, line in enumerate(lines) if UNRELEASED.match(line)),
            None,
        )
        if unreleased is not None:
            insert_at = next(
                (i for i in range(unreleased + 1, len(lines)) if lines[i].startswith("## ")),
                len(lines),
            )
        else:
            insert_at = next((i for i, line in enumerate(lines) if line.startswith("## ")), len(lines))
        text = _splice(lines[:insert_at], entry_lines, lines[insert_at:])

    if path.exists() and text == original:
        return False
    path.write_text(text, encoding="utf-8")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate release notes from git history.")
    parser.add_argument("--tag", required=True, help="Release tag being published, for example v0.2.0")
    parser.add_argument("--previous", help="Previous tag (default: nearest reachable tag)")
    parser.add_argument("--repo", help='GitHub repository as owner/name, for example "ark-7/verso"')
    parser.add_argument("--date", help="Release date (default: commit date of the tag)")
    parser.add_argument(
        "--notes",
        action="store_true",
        help="Emit only the release body, without the version heading",
    )
    output = parser.add_mutually_exclusive_group()
    output.add_argument("-o", "--output", metavar="PATH", help="Write the text to PATH")
    output.add_argument(
        "--write",
        metavar="PATH",
        help="Insert or replace this version's section in a changelog file",
    )
    args = parser.parse_args(argv)

    if args.write and args.notes:
        parser.error("--write cannot be combined with --notes")

    target = resolve(args.tag)
    previous = args.previous or previous_tag(target)
    if previous:
        resolve(previous)
    repo = args.repo or detect_repo()
    released = args.date or release_date(target)
    commits = collect(previous, target)

    if args.write:
        entry = render_entry(commits, repo, previous, args.tag, released)
        changed = update_changelog(Path(args.write), entry, args.tag.removeprefix("v"))
        state = "updated" if changed else "already up to date"
        print(f"{args.write}: {state}", file=sys.stderr)
        return 0

    text = (
        render_notes(commits, repo, previous, args.tag)
        if args.notes
        else render_entry(commits, repo, previous, args.tag, released)
    )
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"wrote {args.output}", file=sys.stderr)
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
