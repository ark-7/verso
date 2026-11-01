from verso.export.markdown import render_markdown
from verso.export.pdf import render_pdf
from verso.store import StoryProject


def _make_project(tmp_path):
    project = StoryProject.create(
        tmp_path / "ferryman",
        title="The Ferryman",
        author="A. Author",
        premise="A river, a debt.",
    )
    project.add_chapter(
        1, "The Storm", "Mara rowed into the dark.\n\n*The river* was **cold**."
    )
    project.add_chapter(2, "The Shore", "Dawn came.")
    return project


def test_markdown_export(tmp_path):
    project = _make_project(tmp_path)
    text = render_markdown(project)

    assert "# The Ferryman" in text
    assert "*by A. Author*" in text
    assert "> A river, a debt." in text
    assert "# Chapter 1: The Storm" in text
    assert text.index("# Chapter 1") < text.index("# Chapter 2")


def test_markdown_frontmatter(tmp_path):
    project = _make_project(tmp_path)
    text = render_markdown(project, frontmatter=True)

    assert text.startswith("---\n")
    assert 'title: "The Ferryman"' in text


def test_pdf_export(tmp_path):
    project = _make_project(tmp_path)
    out = render_pdf(project)

    assert out.is_file()
    assert out.read_bytes().startswith(b"%PDF")
    assert out.stat().st_size > 1500
