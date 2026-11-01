import asyncio

from verso import tui
from verso.config import Config
from verso.models import OutlineEntry
from verso.providers.base import GenerationRequest, Provider
from verso.store import StoryProject
from verso.tui import VersoApp


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


def _project(tmp_path, *, outline=True):
    project = StoryProject.create(tmp_path / "story", title="The Ferryman")
    project.meta.genre = "Literary noir"
    project.meta.tone = "Bleak"
    if outline:
        project.meta.outline = [
            OutlineEntry(title="The Storm", summary="A crossing goes wrong.")
        ]
    project.save_meta()
    return project


def test_tui_mounts_and_shows_the_story(tmp_path):
    project = _project(tmp_path)

    async def scenario():
        app = VersoApp(project, FakeProvider())
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            assert "The Ferryman" in str(app._topbar_text())
            assert "Literary noir" in str(app._topbar_text())
            app._handle_command("/help")
            app._handle_command("/progress")
            app._handle_command("/plan")

    asyncio.run(scenario())


def test_tui_write_collects_a_direction_and_writes_the_chapter(tmp_path, monkeypatch):
    project = _project(tmp_path)
    monkeypatch.setattr(
        tui, "load_config", lambda: Config(words_per_chapter=100)
    )
    provider = FakeProvider(["The ledger ", "curled and blackened."])

    async def scenario():
        app = VersoApp(project, provider)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app._handle_command("/write 1")
            assert app.pending == ("direction", 1, False)
            command = app.query_one("#command")
            command.value = "Mara burns the ledger."
            await pilot.press("enter")
            for _ in range(100):
                await pilot.pause(0.05)
                if not app._busy:
                    break
            assert not app._busy

    asyncio.run(scenario())

    assert project.meta.outline[0].direction == "Mara burns the ledger."
    assert project.chapter(1).body == "The ledger curled and blackened."
    assert "Mara burns the ledger." in provider.requests[0].prompt


def test_tui_plain_text_guides_the_next_chapter(tmp_path):
    project = _project(tmp_path)

    async def scenario():
        app = VersoApp(project, FakeProvider())
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app._handle_free_text("The ferry capsizes.")

    asyncio.run(scenario())

    assert project.meta.outline[0].direction == "The ferry capsizes."


def test_tui_plain_text_without_a_plan_saves_story_notes(tmp_path):
    project = _project(tmp_path, outline=False)

    async def scenario():
        app = VersoApp(project, FakeProvider())
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app._handle_free_text("The river remembers every debt.")

    asyncio.run(scenario())

    assert project.meta.notes == "The river remembers every debt."


def test_tui_character_and_plan_editing_prompts_are_sequential(tmp_path):
    project = _project(tmp_path)

    async def scenario():
        app = VersoApp(project, FakeProvider())
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app._handle_command("/characters add")
            assert app.pending == ("char_name",)
            command = app.query_one("#command")
            command.value = "Mara"
            await pilot.press("enter")
            assert app.pending == ("char_role", "Mara")
            command.value = "protagonist"
            await pilot.press("enter")
            assert app.pending == ("char_description", "Mara", "protagonist")
            command.value = "A ferryman with a debt."
            await pilot.press("enter")
            assert app.pending is None

            app._handle_command("/edit 1")
            assert app.pending == ("plan_title", 1)
            command.value = "The Crossing"
            await pilot.press("enter")
            assert app.pending == ("plan_summary", 1)
            command.value = "The boat goes down."
            await pilot.press("enter")
            assert app.pending == ("plan_direction", 1)
            command.value = "Mara saves the child first."
            await pilot.press("enter")
            assert app.pending is None

    asyncio.run(scenario())

    assert project.meta.characters[0].name == "Mara"
    assert project.meta.characters[0].role == "protagonist"
    assert project.meta.characters[0].description == "A ferryman with a debt."
    assert project.meta.outline[0].title == "The Crossing"
    assert project.meta.outline[0].summary == "The boat goes down."
    assert project.meta.outline[0].direction == "Mara saves the child first."
