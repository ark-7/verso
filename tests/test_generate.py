import pytest

from verso.generate import (
    GenerationError,
    accept_review_suggestions,
    chapter_word_target,
    clear_accepted_suggestions,
    generate_outline,
    review_idea,
    write_chapter,
)
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


def test_write_chapter_in_screenplay_mode_uses_fountain(tmp_path):
    project = StoryProject.create(
        tmp_path / "script", title="The Ferryman", mode="screenplay"
    )
    project.meta.outline = [OutlineEntry(title="Cold Open")]
    project.save_meta()
    provider = FakeProvider(["INT. FERRY - NIGHT\n\nMARA\nI owe nothing."])

    chapter = write_chapter(project, provider, 1, words=100)

    assert chapter.path.name == "001-cold-open.fountain"
    assert "Fountain" in provider.requests[0].system
    assert "scene heading" in provider.requests[0].prompt.lower()
    assert "screenplay page" in provider.requests[0].prompt.lower()


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


def test_chapter_word_target_precedence(tmp_path):
    project = _project(tmp_path)
    project.meta.words_per_chapter = 1200

    assert chapter_word_target(project, 1, fallback=900) == 1200

    project.meta.outline[0].words = 700
    assert chapter_word_target(project, 1, fallback=900) == 700
    assert chapter_word_target(project, 1, explicit=500, fallback=900) == 500

    project.meta.outline[0].words = None
    project.meta.words_per_chapter = None
    assert chapter_word_target(project, 1, fallback=900) == 900


def test_write_chapter_can_disable_continuity(tmp_path):
    project = _project(tmp_path)
    write_chapter(project, FakeProvider(["Mara rowed into the dark."]), 1, words=100)
    provider = FakeProvider(["Dawn came."])

    write_chapter(project, provider, 2, words=100, continuity=False)

    assert "Mara rowed into the dark." not in provider.requests[0].prompt
    assert "direct continuity" not in provider.requests[0].prompt


def test_review_idea_stores_advice_without_applying_it(tmp_path):
    project = _project(tmp_path)
    provider = FakeProvider(
        [
            "What works\nThe debt premise is strong.\n\n",
            "Questions\n\n1. Why does Mara stay?\n\n",
            "Optional suggestions\n\n1. Give Mara a physical tell.\n",
        ]
    )

    review = review_idea(project, provider, premise="A ferryman owes a debt.")

    assert review.text.startswith("What works")
    assert review.suggestions == ["Give Mara a physical tell."]
    assert project.meta.accepted_suggestions == []
    assert "A ferryman owes a debt." in provider.requests[0].prompt
    reloaded = StoryProject.load(project.root)
    assert reloaded.meta.idea_review.suggestions == [
        "Give Mara a physical tell."
    ]


def test_accept_review_suggestions_requires_an_explicit_choice(tmp_path):
    project = _project(tmp_path)
    provider = FakeProvider(
        [
            "What works\nStrong.\n\nQuestions\n\n1. Why now?\n\n",
            (
                "Optional suggestions\n\n1. Give Mara a tell.\n"
                "2. Let the river answer.\n"
            ),
        ]
    )
    review_idea(project, provider)

    accepted = accept_review_suggestions(project, [2])

    assert accepted == ["Let the river answer."]
    assert project.meta.accepted_suggestions == ["Let the river answer."]
    assert "Give Mara a tell." not in project.meta.accepted_suggestions
    reloaded = StoryProject.load(project.root)
    assert reloaded.meta.accepted_suggestions == ["Let the river answer."]


def test_accept_review_suggestions_can_include_author_wording(tmp_path):
    project = _project(tmp_path)
    review_idea(
        project,
        FakeProvider(["Optional suggestions\n\n1. Give Mara a tell.\n"]),
    )

    accepted = accept_review_suggestions(
        project, [], enhancement="Mara hums when she is afraid."
    )

    assert accepted == ["Mara hums when she is afraid."]
    assert project.meta.accepted_suggestions == ["Mara hums when she is afraid."]


def test_clear_accepted_suggestions_restores_the_authors_original_bible(tmp_path):
    project = _project(tmp_path)
    project.meta.accepted_suggestions = ["Give Mara a tell."]
    project.save_meta()

    removed = clear_accepted_suggestions(project)

    assert removed == 1
    assert project.meta.accepted_suggestions == []
    assert StoryProject.load(project.root).meta.accepted_suggestions == []


def test_accept_review_suggestions_rejects_unknown_numbers(tmp_path):
    project = _project(tmp_path)
    review_idea(
        project,
        FakeProvider(["Optional suggestions\n\n1. Give Mara a tell.\n"]),
    )

    with pytest.raises(GenerationError, match="does not exist"):
        accept_review_suggestions(project, [4])
