from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "changelog.py"

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test Author",
    "GIT_AUTHOR_EMAIL": "author@example.com",
    "GIT_COMMITTER_NAME": "Test Author",
    "GIT_COMMITTER_EMAIL": "author@example.com",
    "GIT_AUTHOR_DATE": "2024-01-01T12:00:00+0000",
    "GIT_COMMITTER_DATE": "2024-01-01T12:00:00+0000",
}


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env=os.environ | GIT_ENV,
    )
    return result.stdout


def commit(repo: Path, message: str) -> None:
    (repo / "story.txt").write_text(message + "\n", encoding="utf-8")
    git(repo, "add", "story.txt")
    git(repo, "commit", "-m", message)


def run(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-b", "main")
    return tmp_path


def test_groups_conventional_commits(repo: Path) -> None:
    commit(repo, "feat: add markdown export")
    commit(repo, "fix(store): handle empty chapters")
    commit(repo, "docs: explain screenplay mode")
    commit(repo, "Random housekeeping")
    git(repo, "tag", "-a", "v0.1.0", "-m", "Verso 0.1.0")

    result = run(repo, "--tag", "v0.1.0", "--repo", "acme/verso", "--date", "2024-01-02")

    assert result.stdout.startswith("## [0.1.0](")
    assert "### Added\n- Add markdown export" in result.stdout
    assert "### Fixed\n- **store**: Handle empty chapters" in result.stdout
    assert "### Documentation\n- Explain screenplay mode" in result.stdout
    assert "### Other Changes\n- Random housekeeping" in result.stdout
    assert "**Full Changelog**: https://github.com/acme/verso/commits/v0.1.0" in result.stdout


def test_uses_previous_tag_and_compare_link(repo: Path) -> None:
    commit(repo, "feat: first feature")
    git(repo, "tag", "-a", "v0.1.0", "-m", "v0.1.0")
    commit(repo, "fix: second fix")
    git(repo, "tag", "-a", "v0.1.1", "-m", "v0.1.1")

    result = run(repo, "--tag", "v0.1.1", "--repo", "acme/verso", "--date", "2024-02-02")

    assert "Second fix" in result.stdout
    assert "First feature" not in result.stdout
    assert "compare/v0.1.0...v0.1.1" in result.stdout


def test_explicit_previous_tag(repo: Path) -> None:
    commit(repo, "feat: one")
    git(repo, "tag", "-a", "v0.1.0", "-m", "v0.1.0")
    commit(repo, "feat: two")
    git(repo, "tag", "-a", "v0.2.0", "-m", "v0.2.0")

    result = run(repo, "--tag", "v0.2.0", "--previous", "v0.1.0", "--repo", "acme/verso")

    assert "Two" in result.stdout
    assert "One" not in result.stdout


def test_breaking_changes_are_separated(repo: Path) -> None:
    commit(repo, "feat!: drop legacy config")
    commit(repo, "fix(core): repair loader\n\nBREAKING CHANGE: pass a Path instead of str")
    git(repo, "tag", "-a", "v0.1.0", "-m", "v0.1.0")

    result = run(repo, "--tag", "v0.1.0", "--repo", "acme/verso", "--date", "2024-01-02")

    breaking = result.stdout.split("### Breaking Changes", 1)[1].split("### ", 1)[0]
    assert "Drop legacy config" in breaking
    assert "Repair loader" in breaking
    assert "### Added" not in result.stdout
    assert "### Fixed" not in result.stdout


def test_release_and_changelog_commits_are_skipped(repo: Path) -> None:
    commit(repo, "feat: real change")
    commit(repo, "chore(release): 0.2.0")
    commit(repo, "docs(changelog): update for v0.2.0")
    git(repo, "tag", "-a", "v0.2.0", "-m", "v0.2.0")

    result = run(repo, "--tag", "v0.2.0", "--repo", "acme/verso", "--date", "2024-01-02")

    bullets = [line for line in result.stdout.splitlines() if line.startswith("- ")]
    assert len(bullets) == 1
    assert "Real change" in bullets[0]


def test_changelog_only_commits_do_not_appear(repo: Path) -> None:
    commit(repo, "feat: real change")
    (repo / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")
    git(repo, "add", "CHANGELOG.md")
    git(repo, "commit", "-m", "docs: regenerate changelog")
    git(repo, "tag", "-a", "v0.1.0", "-m", "v0.1.0")

    result = run(repo, "--tag", "v0.1.0", "--repo", "acme/verso", "--date", "2024-01-02")

    assert "Regenerate changelog" not in result.stdout
    assert "Real change" in result.stdout


def test_write_creates_changelog_and_is_idempotent(repo: Path) -> None:
    commit(repo, "feat: add fountain export")
    git(repo, "tag", "-a", "v0.1.0", "-m", "v0.1.0")
    args = (
        "--tag",
        "v0.1.0",
        "--repo",
        "acme/verso",
        "--date",
        "2024-01-02",
        "--write",
        "CHANGELOG.md",
    )

    run(repo, *args)
    first = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    assert first.startswith("# Changelog")
    assert "## [0.1.0](" in first
    assert "### Added\n- Add fountain export" in first

    result = run(repo, *args)
    assert "already up to date" in result.stderr
    assert (repo / "CHANGELOG.md").read_text(encoding="utf-8") == first


def test_write_preserves_other_versions_and_orders_newest_first(repo: Path) -> None:
    (repo / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [0.0.1] - 2020-01-01\n\n### Added\n- Old thing\n",
        encoding="utf-8",
    )
    commit(repo, "feat: new thing")
    git(repo, "tag", "-a", "v0.1.0", "-m", "v0.1.0")

    run(
        repo,
        "--tag",
        "v0.1.0",
        "--repo",
        "acme/verso",
        "--date",
        "2024-01-02",
        "--write",
        "CHANGELOG.md",
    )

    text = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "Old thing" in text
    assert text.index("## [0.1.0]") < text.index("## [0.0.1]")


def test_notes_omit_the_version_heading(repo: Path) -> None:
    commit(repo, "feat: add pdf export")
    git(repo, "tag", "-a", "v0.1.0", "-m", "v0.1.0")

    result = run(
        repo,
        "--tag",
        "v0.1.0",
        "--repo",
        "acme/verso",
        "--date",
        "2024-01-02",
        "--notes",
    )

    assert "## [" not in result.stdout
    assert "### Added\n- Add pdf export" in result.stdout


def test_output_file(repo: Path) -> None:
    commit(repo, "feat: write notes")
    git(repo, "tag", "-a", "v0.1.0", "-m", "v0.1.0")

    result = run(
        repo,
        "--tag",
        "v0.1.0",
        "--repo",
        "acme/verso",
        "--date",
        "2024-01-02",
        "--notes",
        "-o",
        "NOTES.md",
    )

    assert result.stdout == ""
    assert "### Added" in (repo / "NOTES.md").read_text(encoding="utf-8")


def test_default_release_date_uses_the_tag_commit(repo: Path) -> None:
    commit(repo, "feat: dated")
    git(repo, "tag", "-a", "v0.1.0", "-m", "v0.1.0")

    result = run(repo, "--tag", "v0.1.0", "--repo", "acme/verso")

    assert "- 2024-01-01" in result.stdout
