import pytest

from verso.generate import GenerationError, generate_outline, write_chapter
from verso.models import OutlineEntry
from verso.providers.base import GenerationRequest, Provider, ProviderError
from verso.store import StoryProject


class FakeProvider(Provider):
    name = "fake"
    default_model = "fake-1"

    def __init__(self, chunks, *, interrupt=False):
        super().__init__()
        self.chunks = list(chunks)
        self.interrupt = interrupt
        self.requests: list[GenerationRequest] = []

    def stream(self, request):
        self.requests.append(request)
        yield from self.chunks
        if self.interrupt:
            raise KeyboardInterrupt


class FailingProvider(Provider):
    name = "failing"
    default_model = "failing-1"

    def __init__(self, chunks=(), error=None):
        super().__init__()
        self.chunks = list(chunks)
        self.error = error or ProviderError("boom")

    def stream(self, request):
        yield from self.chunks
        raise self.error


def _project(tmp_path, entries=None):
    project = StoryProject.create(tmp_path / "story", title="The Ferryman")
    project.meta.outline = entries or [
        OutlineEntry(title="The Storm", summary="A crossing goes wrong."),
        OutlineEntry(title="The Shore", summary="Dawn reveals the truth."),
    ]
    project.save_meta()
    return project


def test_generate_outline_saves_entries(tmp_path):
    project = StoryProject.create(tmp_path / "story", title="The Ferryman")
    provider = FakeProvider(
        ["1. The Storm — A crossing goes wrong.\n", "2. The Shore — Dawn comes."]
    )

    entries = generate_outline(project, provider, chapters=2)

    assert [entry.title for entry in entries] == ["The Storm", "The Shore"]
    assert "Build a 2-chapter outline" in provider.requests[0].prompt
    assert "The Ferryman" in provider.requests[0].system
    reloaded = StoryProject.load(tmp_path / "story")
    assert len(reloaded.meta.outline) == 2


def test_generate_outline_rejects_unparseable_output(tmp_path):
    project = StoryProject.create(tmp_path / "story", title="T")
    with pytest.raises(GenerationError, match="usable outline"):
        generate_outline(project, FakeProvider(["I could not do that."]), chapters=3)


def test_write_chapter_streams_and_saves(tmp_path):
    project = _project(tmp_path)
    tokens: list[str] = []
    provider = FakeProvider(["Mara ", "rowed.\n\n", "She waited."])

    chapter = write_chapter(project, provider, 1, words=100, on_token=tokens.append)

    assert chapter.title == "Chapter 1: The Storm"
    assert chapter.body == "Mara rowed.\n\nShe waited."
    assert chapter.words == 4
    assert tokens == ["Mara ", "rowed.\n\n", "She waited."]
    assert provider.requests[0].max_tokens == 1020
    assert "The Storm" in provider.requests[0].system
    assert list(project.chapters_dir.glob("*.part")) == []


def test_write_chapter_refuses_to_overwrite(tmp_path):
    project = _project(tmp_path)
    write_chapter(project, FakeProvider(["First."]), 1, words=100)

    with pytest.raises(GenerationError, match="already exists"):
        write_chapter(project, FakeProvider(["Second."]), 1, words=100)

    write_chapter(project, FakeProvider(["Second."]), 1, words=100, force=True)
    assert project.chapter(1).body == "Second."


def test_write_chapter_includes_previous_tail(tmp_path):
    project = _project(tmp_path)
    write_chapter(project, FakeProvider(["Mara rowed into the dark."]), 1, words=100)
    provider = FakeProvider(["Dawn came."])

    write_chapter(project, provider, 2, words=100)

    assert "Mara rowed into the dark." in provider.requests[0].prompt


def test_write_chapter_keeps_partial_text_on_interrupt(tmp_path):
    project = _project(tmp_path)
    chapter = write_chapter(
        project, FakeProvider(["Partial text."], interrupt=True), 1, words=100
    )

    assert chapter.body == "Partial text."
    assert chapter.path.is_file()


def test_write_chapter_removes_empty_part_file_on_provider_failure(tmp_path):
    project = _project(tmp_path)

    with pytest.raises(ProviderError, match="boom"):
        write_chapter(project, FailingProvider(), 1, words=100)

    assert list(project.chapters_dir.iterdir()) == []


def test_write_chapter_keeps_partial_text_on_provider_failure(tmp_path):
    project = _project(tmp_path)

    with pytest.raises(ProviderError, match="boom"):
        write_chapter(project, FailingProvider(["Mara rowed."]), 1, words=100)

    parts = list(project.chapters_dir.glob("*.part"))
    assert len(parts) == 1
    assert "Mara rowed." in parts[0].read_text(encoding="utf-8")
    assert list(project.chapters_dir.glob("*.md")) == []


def test_write_chapter_includes_the_users_direction(tmp_path):
    project = _project(
        tmp_path,
        entries=[
            OutlineEntry(
                title="The Storm",
                summary="A crossing goes wrong.",
                direction="Mara sets the ledger on fire.",
            )
        ],
    )
    provider = FakeProvider(["The ledger curled and blackened."])

    write_chapter(project, provider, 1, words=100)

    prompt = provider.requests[0].prompt
    assert "Mara sets the ledger on fire." in prompt
    assert "takes precedence" in prompt


def test_write_chapter_without_outline_fails(tmp_path):
    project = StoryProject.create(tmp_path / "story", title="T")
    with pytest.raises(GenerationError, match="outline"):
        write_chapter(project, FakeProvider(["x"]), 1)


def test_write_chapter_outside_outline_fails(tmp_path):
    project = _project(tmp_path)
    with pytest.raises(GenerationError, match="outside the outline"):
        write_chapter(project, FakeProvider(["x"]), 5)


def test_write_chapter_with_empty_output_fails(tmp_path):
    project = _project(tmp_path)
    with pytest.raises(GenerationError, match="no text"):
        write_chapter(project, FakeProvider([]), 1)
    assert list(project.chapters_dir.glob("*.md")) == []
