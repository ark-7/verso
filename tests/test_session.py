from verso import session
from verso.config import Config
from verso.models import OutlineEntry
from verso.providers.base import GenerationRequest, Provider
from verso.store import StoryProject


class FakeProvider(Provider):
    name = "fake"
    default_model = "fake-1"

    def __init__(self, chunks=("Prose.",)):
        super().__init__()
        self.chunks = list(chunks)
        self.requests: list[GenerationRequest] = []

    def stream(self, request):
        self.requests.append(request)
        yield from self.chunks


def _prompt_sequence(monkeypatch, answers):
    remaining = iter(answers)
    monkeypatch.setattr(session.typer, "prompt", lambda *args, **kwargs: next(remaining))


def test_edit_preferences_saves_every_answer(tmp_path, monkeypatch):
    project = StoryProject.create(tmp_path / "story", title="Old Title")
    _prompt_sequence(
        monkeypatch,
        [
            "The Ferryman",
            "A. Author",
            "Literary noir",
            "Bleak",
            "A ferryman discovers a debt she never owed.",
            "1",
            "present",
            "Mara rows into a storm.",
            "She pays the debt with her own name.",
            "The Odyssey, Dune",
            "The river remembers every debt.",
            "8",
            "pdf",
        ],
    )

    session._edit_preferences(project)

    meta = StoryProject.load(project.root).meta
    assert meta.title == "The Ferryman"
    assert meta.author == "A. Author"
    assert meta.genre == "Literary noir"
    assert meta.tone == "Bleak"
    assert meta.logline == "A ferryman discovers a debt she never owed."
    assert meta.pov.value == "first"
    assert meta.tense == "present"
    assert meta.opening == "Mara rows into a storm."
    assert meta.ending == "She pays the debt with her own name."
    assert meta.references == ["The Odyssey", "Dune"]
    assert meta.notes == "The river remembers every debt."
    assert meta.target_chapters == 8
    assert meta.export_format == "pdf"


def test_session_menu_can_be_left_without_changes(tmp_path, monkeypatch):
    project = StoryProject.create(tmp_path / "story", title="The Ferryman")
    project.meta.outline = [OutlineEntry(title="The Storm")]
    project.save_meta()
    _prompt_sequence(monkeypatch, ["q"])

    session.run_session(project, FakeProvider(), setup=False, tui=False)

    assert project.meta.outline[0].title == "The Storm"


def test_write_next_records_direction_and_writes_the_chapter(tmp_path, monkeypatch):
    project = StoryProject.create(tmp_path / "story", title="The Ferryman")
    project.meta.outline = [
        OutlineEntry(title="The Storm", summary="A crossing goes wrong.")
    ]
    project.save_meta()
    _prompt_sequence(monkeypatch, ["Mara burns the ledger."])
    monkeypatch.setattr(
        session, "load_config", lambda: Config(words_per_chapter=100)
    )
    provider = FakeProvider(["The ledger curled and blackened."])

    session._write_next(project, provider)

    assert project.meta.outline[0].direction == "Mara burns the ledger."
    assert project.chapter(1).body == "The ledger curled and blackened."
    assert "Mara burns the ledger." in provider.requests[0].prompt


def test_setup_wizard_saves_preferences_and_generates_an_outline(
    tmp_path, monkeypatch
):
    project = StoryProject.create(tmp_path / "story", title="The Ferryman")
    _prompt_sequence(
        monkeypatch,
        [
            "The Ferryman",
            "",
            "Literary noir",
            "Bleak",
            "A ferryman discovers a debt she never owed.",
            "1",
            "past",
            "A storm strands her.",
            "She pays with her name.",
            "The Odyssey",
            "Keep the river ambiguous.",
            "2",
            "md",
            "0",  # leave the character manager
        ],
    )
    monkeypatch.setattr(session.typer, "confirm", lambda *args, **kwargs: True)
    provider = FakeProvider(
        ["1. The Storm — A crossing goes wrong.\n", "2. The Shore — Dawn comes."]
    )

    session._setup_story(project, provider)

    reloaded = StoryProject.load(project.root)
    assert reloaded.meta.opening == "A storm strands her."
    assert reloaded.meta.ending == "She pays with her name."
    assert reloaded.meta.notes == "Keep the river ambiguous."
    assert reloaded.meta.target_chapters == 2
    assert [entry.title for entry in reloaded.meta.outline] == [
        "The Storm",
        "The Shore",
    ]


def test_character_manager_adds_a_character(tmp_path, monkeypatch):
    project = StoryProject.create(tmp_path / "story", title="The Ferryman")
    _prompt_sequence(
        monkeypatch,
        ["1", "Mara", "protagonist", "A ferryman with a debt.", "0"],
    )

    session._manage_characters(project)

    assert [character.name for character in project.meta.characters] == ["Mara"]
    reloaded = StoryProject.load(project.root)
    assert reloaded.meta.characters[0].description == "A ferryman with a debt."
