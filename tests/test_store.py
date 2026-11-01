import pytest

from verso.models import OutlineEntry
from verso.store import AmbiguousStoryError, StoryError, StoryProject


def test_create_and_reload(tmp_path):
    root = tmp_path / "the-ferryman"
    StoryProject.create(root, title="The Ferryman", author="A. Author")

    assert (root / "story.json").is_file()
    assert (root / "chapters").is_dir()
    assert (root / "exports").is_dir()

    reloaded = StoryProject.load(root / "chapters")
    assert reloaded.meta.title == "The Ferryman"
    assert reloaded.meta.author == "A. Author"
    assert reloaded.root == root.resolve()


def test_story_preferences_roundtrip(tmp_path):
    root = tmp_path / "story"
    project = StoryProject.create(root, title="The Ferryman")
    project.meta.opening = "A storm."
    project.meta.ending = "She pays with her name."
    project.meta.references = ["The Odyssey"]
    project.meta.notes = "Keep the river ambiguous."
    project.meta.target_chapters = 8
    project.meta.export_format = "pdf"
    project.meta.outline = [
        OutlineEntry(title="The Storm", direction="The ferry capsizes.")
    ]
    project.save_meta()

    reloaded = StoryProject.load(root)

    assert reloaded.meta.opening == "A storm."
    assert reloaded.meta.ending == "She pays with her name."
    assert reloaded.meta.references == ["The Odyssey"]
    assert reloaded.meta.notes == "Keep the river ambiguous."
    assert reloaded.meta.target_chapters == 8
    assert reloaded.meta.export_format == "pdf"
    assert reloaded.meta.outline[0].direction == "The ferry capsizes."


def test_chapter_roundtrip(tmp_path):
    project = StoryProject.create(tmp_path / "story", title="T")
    project.add_chapter(1, "The Storm", "Mara rowed.\n\nShe waited.")

    chapter = project.chapter(1)
    assert chapter.title == "Chapter 1: The Storm"
    assert chapter.body == "Mara rowed.\n\nShe waited."
    assert chapter.words == 4
    assert chapter.path.name == "001-the-storm.md"


def test_next_chapter_number_handles_gaps(tmp_path):
    project = StoryProject.create(tmp_path / "story", title="T")
    project.add_chapter(1, "One", "a")
    project.add_chapter(3, "Three", "c")

    assert project.next_chapter_number() == 4
    assert [chapter.number for chapter in project.chapters()] == [1, 3]


def test_discover_finds_stories_up_to_two_levels(tmp_path):
    StoryProject.create(tmp_path / "one", title="One")
    StoryProject.create(tmp_path / "group" / "two", title="Two")

    found = StoryProject.discover(tmp_path)

    assert [project.meta.title for project in found] == ["One", "Two"]


def test_discover_skips_hidden_and_build_dirs(tmp_path):
    StoryProject.create(tmp_path / ".hidden", title="Hidden")
    StoryProject.create(tmp_path / "__pycache__", title="Cache")

    assert StoryProject.discover(tmp_path) == []


def test_discover_does_not_descend_into_a_story(tmp_path):
    StoryProject.create(tmp_path / "outer", title="Outer")
    StoryProject.create(tmp_path / "outer" / "nested" / "inner", title="Inner")

    found = StoryProject.discover(tmp_path)

    assert [project.meta.title for project in found] == ["Outer"]


def test_discover_respects_depth(tmp_path):
    StoryProject.create(tmp_path / "a" / "b" / "deep", title="Deep")

    assert StoryProject.discover(tmp_path) == []
    assert [project.meta.title for project in StoryProject.discover(tmp_path, depth=3)] == [
        "Deep"
    ]


def test_resolve_auto_selects_single_nearby_story(tmp_path):
    StoryProject.create(tmp_path / "stories" / "one", title="One")

    assert StoryProject.resolve(tmp_path).meta.title == "One"


def test_resolve_prefers_a_story_in_the_current_tree(tmp_path):
    outer = StoryProject.create(tmp_path / "outer", title="Outer")

    assert StoryProject.resolve(outer.root / "chapters").meta.title == "Outer"


def test_resolve_reports_ambiguous_stories(tmp_path):
    StoryProject.create(tmp_path / "a", title="A")
    StoryProject.create(tmp_path / "b", title="B")

    with pytest.raises(AmbiguousStoryError) as excinfo:
        StoryProject.resolve(tmp_path)

    assert {path.name for path in excinfo.value.candidates} == {"a", "b"}
    assert "Multiple stories found nearby" in str(excinfo.value)


def test_resolve_without_any_story(tmp_path):
    with pytest.raises(StoryError, match="No story found"):
        StoryProject.resolve(tmp_path)
