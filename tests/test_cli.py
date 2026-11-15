import json
from datetime import UTC, datetime

import pytest
from typer.testing import CliRunner

from verso import cli
from verso.cli import app
from verso.config import Config, save_config
from verso.health import ProviderHealth
from verso.models import IdeaReview, OutlineEntry
from verso.providers.base import Provider, ProviderError
from verso.providers.ollama import OllamaModel
from verso.store import StoryProject

runner = CliRunner()


class _FakeProvider(Provider):
    name = "ollama"
    default_model = "fake"

    def stream(self, request):
        yield "Mara rowed into the dark."


def _output(result) -> str:
    return (result.output or "") + (getattr(result, "stderr", "") or "")


def test_list_overview_when_multiple_stories(tmp_path, monkeypatch):
    StoryProject.create(tmp_path / "a", title="Story A")
    StoryProject.create(tmp_path / "b", title="Story B")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["list"])

    assert result.exit_code == 0
    output = _output(result)
    assert "Story A" in output
    assert "Story B" in output
    assert "Multiple stories detected" in output


def test_new_can_create_a_screenplay_story(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_is_interactive", lambda: False)

    result = runner.invoke(
        app,
        [
            "new",
            "The Ferryman",
            "--path",
            str(tmp_path / "script"),
            "--mode",
            "screenplay",
            "--no-guided",
        ],
    )

    assert result.exit_code == 0, _output(result)
    project = StoryProject.load(tmp_path / "script")
    assert project.meta.mode.value == "screenplay"
    assert project.meta.export_format == "fountain"


def test_mode_command_switches_an_existing_story(tmp_path, monkeypatch):
    project = StoryProject.create(tmp_path / "story", title="The Ferryman")
    monkeypatch.chdir(project.root)

    result = runner.invoke(app, ["mode", "screenplay"])

    assert result.exit_code == 0, _output(result)
    reloaded = StoryProject.load(project.root)
    assert reloaded.meta.mode.value == "screenplay"
    assert reloaded.meta.export_format == "fountain"
    assert reloaded.chapter_suffix == ".fountain"


def test_export_defaults_to_fountain_for_a_screenplay(tmp_path, monkeypatch):
    project = StoryProject.create(
        tmp_path / "script", title="The Ferryman", mode="screenplay"
    )
    project.add_chapter(1, "Cold Open", "INT. FERRY - NIGHT\n\nMARA\nI owe nothing.")
    monkeypatch.chdir(project.root)

    result = runner.invoke(app, ["export"])

    assert result.exit_code == 0, _output(result)
    assert (project.exports_dir / "the-ferryman.fountain").is_file()


def test_words_command_sets_story_and_chapter_targets(tmp_path, monkeypatch):
    project = StoryProject.create(tmp_path / "story", title="The Ferryman")
    project.meta.outline = [
        OutlineEntry(title="The Storm"),
        OutlineEntry(title="The Shore"),
    ]
    project.save_meta()
    monkeypatch.chdir(project.root)

    result = runner.invoke(app, ["words", "1200"])

    assert result.exit_code == 0, _output(result)
    assert StoryProject.load(project.root).meta.words_per_chapter == 1200

    result = runner.invoke(app, ["words", "800", "--chapter", "2"])

    assert result.exit_code == 0, _output(result)
    reloaded = StoryProject.load(project.root)
    assert reloaded.meta.outline[1].words == 800
    assert reloaded.meta.outline[0].words is None

    result = runner.invoke(app, ["words", "--chapter", "2", "--clear"])

    assert result.exit_code == 0, _output(result)
    assert StoryProject.load(project.root).meta.outline[1].words is None


def test_continuity_command_toggles_the_story_setting(tmp_path, monkeypatch):
    project = StoryProject.create(tmp_path / "story", title="The Ferryman")
    monkeypatch.chdir(project.root)

    result = runner.invoke(app, ["continuity", "off"])

    assert result.exit_code == 0, _output(result)
    assert StoryProject.load(project.root).meta.continuity is False

    result = runner.invoke(app, ["continuity"])

    assert result.exit_code == 0, _output(result)
    assert "off" in _output(result)


def test_review_accepts_only_the_named_saved_suggestion(tmp_path, monkeypatch):
    project = StoryProject.create(tmp_path / "story", title="The Ferryman")
    project.meta.idea_review = IdeaReview(
        text="What works\nStrong premise.",
        suggestions=["Give Mara a tell.", "Let the river answer."],
        reviewed_at=datetime.now(UTC),
    )
    project.save_meta()
    monkeypatch.chdir(project.root)

    result = runner.invoke(app, ["review", "--accept", "1"])

    assert result.exit_code == 0, _output(result)
    reloaded = StoryProject.load(project.root)
    assert reloaded.meta.accepted_suggestions == ["Give Mara a tell."]


def test_review_command_stores_feedback_without_applying_it(
    tmp_path, monkeypatch
):
    project = StoryProject.create(tmp_path / "story", title="The Ferryman")
    project.meta.logline = "A ferryman discovers a debt she never owed."
    project.save_meta()
    monkeypatch.chdir(project.root)
    monkeypatch.setattr(cli, "_provider", lambda *args: _FakeProvider())
    monkeypatch.setattr(cli, "_is_interactive", lambda: False)

    result = runner.invoke(app, ["review"])

    assert result.exit_code == 0, _output(result)
    reloaded = StoryProject.load(project.root)
    assert "Mara rowed into the dark." in reloaded.meta.idea_review.text
    assert reloaded.meta.accepted_suggestions == []


def test_export_auto_selects_the_only_nearby_story(tmp_path, monkeypatch):
    project = StoryProject.create(tmp_path / "only", title="Only Story")
    project.add_chapter(1, "Start", "Once upon a time.")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["export", "--format", "md"])

    assert result.exit_code == 0, _output(result)
    assert (tmp_path / "only" / "exports" / "only-story.md").is_file()


def test_provider_selection_uses_flags_or_config_not_story_pins(monkeypatch):
    seen = {}
    sentinel = object()

    def fake_get_provider(name=None, model=None):
        seen.update(name=name, model=model)
        return sentinel

    monkeypatch.setattr(cli, "get_provider", fake_get_provider)
    monkeypatch.setattr(cli, "load_config", lambda: Config(provider="anthropic"))

    assert cli._provider(None, None) is sentinel
    assert seen == {"name": "anthropic", "model": None}

    assert cli._provider("ollama", "gemma4:e4b") is sentinel
    assert seen == {"name": "ollama", "model": "gemma4:e4b"}


def test_ollama_prompt_offers_cloud_and_local_and_saves_choice(
    tmp_path, monkeypatch
):
    project = StoryProject.create(tmp_path / "story", title="The Ferryman")
    project.meta.outline = [OutlineEntry(title="The Storm", summary="Rain.")]
    project.save_meta()

    monkeypatch.setenv("VERSO_CONFIG_DIR", str(tmp_path / "config"))
    save_config(Config(provider="ollama"))

    local = [
        OllamaModel(
            name="gemma4:e4b",
            cloud=False,
            size=9_600_000_000,
            parameter_size="8.0B",
        )
    ]
    cloud = [
        OllamaModel(
            name="gemma4:cloud", cloud=True, capabilities=("completion",)
        ),
        OllamaModel(
            name="mistral-large-4:cloud",
            cloud=True,
            capabilities=("completion", "tools"),
        ),
    ]
    monkeypatch.setattr(cli, "list_local_models", lambda host: local)
    monkeypatch.setattr(cli, "list_cloud_models", lambda: cloud)
    monkeypatch.setattr(cli, "_is_interactive", lambda: True)

    seen = {}

    def fake_get_provider(name=None, model=None):
        seen.update(name=name, model=model)
        return _FakeProvider(model or "fake")

    monkeypatch.setattr(cli, "get_provider", fake_get_provider)

    result = runner.invoke(
        app,
        ["write", "--story", str(project.root), "--words", "100"],
        input="cloud\n2\n",
    )

    assert result.exit_code == 0, _output(result)
    assert seen == {"name": "ollama", "model": "mistral-large-4:cloud"}
    config_text = (tmp_path / "config" / "config.toml").read_text(encoding="utf-8")
    assert 'ollama_model = "mistral-large-4:cloud"' in config_text
    assert "1 local model(s) and 2 cloud model(s)" in _output(result)


def test_non_interactive_ollama_prefers_free_local_models(monkeypatch):
    monkeypatch.setattr(
        cli,
        "list_local_models",
        lambda host: [OllamaModel(name="gemma4:e4b", cloud=False)],
    )
    monkeypatch.setattr(
        cli,
        "list_cloud_models",
        lambda: [OllamaModel(name="gemma4:cloud", cloud=True)],
    )
    monkeypatch.setattr(cli, "_is_interactive", lambda: False)

    assert cli._prompt_ollama_model(Config()) == "gemma4:e4b"


def test_cloud_models_need_an_explicit_model_when_not_interactive(monkeypatch):
    monkeypatch.setattr(cli, "list_local_models", lambda host: [])
    monkeypatch.setattr(
        cli,
        "list_cloud_models",
        lambda: [OllamaModel(name="gemma4:cloud", cloud=True)],
    )
    monkeypatch.setattr(cli, "_is_interactive", lambda: False)

    with pytest.raises(ProviderError, match="--model"):
        cli._prompt_ollama_model(Config())


def test_unreachable_ollama_is_reported(monkeypatch):
    monkeypatch.setattr(cli, "list_local_models", lambda host: None)

    with pytest.raises(ProviderError, match="ollama serve"):
        cli._prompt_ollama_model(Config())


def test_healthcheck_prints_usable_summary(monkeypatch):
    results = [
        ProviderHealth(
            "anthropic", "ready", "claude-opus-5-5", "1 models available"
        ),
        ProviderHealth("ollama", "unreachable", "", "ollama serve is down"),
    ]
    monkeypatch.setattr(
        cli,
        "check_providers",
        lambda config, only=None, probe=False: results,
    )

    result = runner.invoke(app, ["healthcheck"])

    assert result.exit_code == 0, _output(result)
    output = _output(result)
    assert "Provider health" in output
    assert "Usable" in output
    assert "1/2" in output


def test_healthcheck_json_output(monkeypatch):
    monkeypatch.setattr(
        cli,
        "check_providers",
        lambda config, only=None, probe=False: [
            ProviderHealth(
                "anthropic",
                "ready",
                "claude-opus-5-5",
                "1 models available",
                limits={"requests": "49 / 50"},
            )
        ],
    )

    result = runner.invoke(app, ["healthcheck", "--json"])

    assert result.exit_code == 0, _output(result)
    payload = json.loads(result.output)
    assert payload[0]["name"] == "anthropic"
    assert payload[0]["usable"] is True
    assert payload[0]["limits"] == {"requests": "49 / 50"}


def test_healthcheck_exits_nonzero_when_nothing_is_usable(monkeypatch):
    monkeypatch.setattr(
        cli,
        "check_providers",
        lambda config, only=None, probe=False: [
            ProviderHealth("anthropic", "no credentials", "", "missing key")
        ],
    )

    result = runner.invoke(app, ["healthcheck"])

    assert result.exit_code == 1


def test_session_command_requires_a_terminal(monkeypatch):
    monkeypatch.setattr(cli, "_is_interactive", lambda: False)

    result = runner.invoke(app, ["session"])

    assert result.exit_code == 1
    assert "needs a terminal" in _output(result)


def test_session_command_opens_the_menu_for_an_existing_story(
    tmp_path, monkeypatch
):
    project = StoryProject.create(tmp_path / "story", title="The Ferryman")
    project.meta.outline = [OutlineEntry(title="The Storm")]
    project.save_meta()
    monkeypatch.setattr(cli, "_is_interactive", lambda: True)
    monkeypatch.setattr(
        cli, "load_config", lambda: Config(provider="anthropic")
    )
    monkeypatch.setattr(
        cli, "get_provider", lambda name=None, model=None: _FakeProvider()
    )

    result = runner.invoke(
        app,
        ["session", "--story", str(project.root), "--no-tui"],
        input="q\n",
    )

    assert result.exit_code == 0, _output(result)
    output = _output(result)
    assert "Verso interactive session" in output
    assert "Session menu" in output
    assert "Session ended" in output


def test_new_is_non_interactive_under_a_pipe(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_is_interactive", lambda: False)

    result = runner.invoke(
        app,
        ["new", "The Ferryman", "--path", str(tmp_path / "story")],
    )

    assert result.exit_code == 0, _output(result)
    assert (tmp_path / "story" / "story.json").is_file()
    assert "Story preferences" not in _output(result)


def test_new_guided_requires_a_terminal_without_no_guided(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_is_interactive", lambda: False)

    result = runner.invoke(
        app,
        [
            "new",
            "The Ferryman",
            "--path",
            str(tmp_path / "story"),
            "--guided",
        ],
    )

    assert result.exit_code == 1
    assert "--guided needs a terminal" in _output(result)


def test_ambiguous_story_fails_with_candidates(tmp_path, monkeypatch):
    StoryProject.create(tmp_path / "a", title="Story A")
    StoryProject.create(tmp_path / "b", title="Story B")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["export", "--format", "md"])

    assert result.exit_code == 1
    output = _output(result)
    assert "Multiple stories found nearby" in output
    assert "Story A" in output
    assert "Story B" in output
