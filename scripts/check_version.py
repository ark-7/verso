#!/usr/bin/env python3
"""Check that Verso's version declarations agree with a release tag."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from packaging.version import InvalidVersion, Version

ROOT = Path(__file__).resolve().parents[1]
INIT = ROOT / "src" / "verso" / "__init__.py"
VERSION_RE = re.compile(r'^__version__\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)


def source_version() -> str:
    match = VERSION_RE.search(INIT.read_text(encoding="utf-8"))
    if match is None:
        raise SystemExit(f"could not find __version__ in {INIT}")
    return match.group(1)


def normalize(value: str, label: str) -> Version:
    try:
        return Version(value)
    except InvalidVersion:
        raise SystemExit(f"{label} is not a valid PEP 440 version: {value!r}") from None


def fail(observed: list[tuple[str, Version]], message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    for label, value in observed:
        print(f"  {label}: {value}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare a release tag, package metadata, and CLI output "
        "against the version in src/verso/__init__.py."
    )
    parser.add_argument("--tag", help="Release tag, for example v0.1.0 or v0.1.1-rc1")
    parser.add_argument("--dist-version", help="Installed distribution version")
    parser.add_argument(
        "--cli-version",
        help='Output of `verso --version`, for example "verso 0.1.0"',
    )
    output = parser.add_mutually_exclusive_group()
    output.add_argument(
        "--print-version",
        action="store_true",
        help="Print the normalized tag (or source) version",
    )
    output.add_argument(
        "--is-prerelease",
        action="store_true",
        help="Print true or false for the normalized tag (or source) version",
    )
    args = parser.parse_args(argv)

    source = normalize(source_version(), "source __version__")
    observed: list[tuple[str, Version]] = [("source __version__", source)]

    tag_version: Version | None = None
    if args.tag:
        raw_tag = args.tag[1:] if args.tag.startswith("v") else args.tag
        tag_version = normalize(raw_tag, "tag")
        observed.append(("tag", tag_version))
        if tag_version != source:
            return fail(
                observed,
                f"tag {args.tag!r} does not match source version {source!s}",
            )

    if args.dist_version:
        distribution = normalize(args.dist_version, "distribution version")
        observed.append(("distribution", distribution))
        if distribution != source:
            return fail(observed, "installed distribution version does not match source")

    if args.cli_version:
        parts = args.cli_version.split()
        if not parts:
            return fail(observed, "CLI version output is empty")
        cli = normalize(parts[-1], "CLI version")
        observed.append(("CLI", cli))
        if cli != source:
            return fail(observed, "`verso --version` does not match source")

    display = tag_version or source
    if args.print_version:
        print(display)
    elif args.is_prerelease:
        print("true" if display.is_prerelease else "false")
    else:
        print(f"version OK: {display}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
