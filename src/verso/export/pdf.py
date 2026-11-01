"""PDF export with ReportLab.

ReportLab is pure Python, so the default install needs no LaTeX, GTK, or
Chromium. The Markdown subset understood here is the subset the writer prompts
are allowed to produce (headings, paragraphs, emphasis, blockquotes, lists)
plus a few common extras.
"""

from __future__ import annotations

import re
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, StyleSheet1
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    HRFlowable,
    PageBreak,
    PageTemplate,
    Paragraph,
    Preformatted,
    Spacer,
)

from ..store import StoryProject, slugify

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_CODE = re.compile(r"`(.+?)`")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_HR = re.compile(r"^(-{3,}|\*{3,}|_{3,})$")
_BULLET = re.compile(r"^\s*[-*]\s+(.*)$")


def _inline(text: str) -> str:
    out = escape(text)
    out = _CODE.sub(lambda m: f'<font name="Courier">{m.group(1)}</font>', out)
    out = _BOLD.sub(r"<b>\1</b>", out)
    out = _ITALIC.sub(r"<i>\1</i>", out)
    out = _LINK.sub(r'<link href="\2">\1</link>', out)
    return out


def _styles() -> StyleSheet1:
    styles = StyleSheet1()
    styles.add(
        ParagraphStyle(
            "Title",
            fontName="Times-Bold",
            fontSize=28,
            leading=34,
            alignment=TA_CENTER,
        )
    )
    styles.add(
        ParagraphStyle(
            "Author",
            fontName="Times-Italic",
            fontSize=13,
            leading=18,
            alignment=TA_CENTER,
            spaceBefore=10,
            textColor=colors.HexColor("#444444"),
        )
    )
    styles.add(
        ParagraphStyle(
            "Logline",
            fontName="Times-Italic",
            fontSize=11.5,
            leading=17,
            alignment=TA_CENTER,
            leftIndent=28 * mm,
            rightIndent=28 * mm,
            spaceBefore=26,
            textColor=colors.HexColor("#333333"),
        )
    )
    styles.add(
        ParagraphStyle(
            "Genre",
            fontName="Times-Roman",
            fontSize=10,
            leading=14,
            alignment=TA_CENTER,
            spaceBefore=18,
            textColor=colors.HexColor("#666666"),
        )
    )
    for level, size, leading, before, after in (
        (1, 20, 26, 0, 16),
        (2, 15, 21, 14, 8),
        (3, 12.5, 18, 12, 6),
    ):
        styles.add(
            ParagraphStyle(
                f"H{level}",
                fontName="Times-Bold",
                fontSize=size,
                leading=leading,
                spaceBefore=before,
                spaceAfter=after,
                keepWithNext=True,
            )
        )
    body = ParagraphStyle(
        "Body",
        fontName="Times-Roman",
        fontSize=11.5,
        leading=17.5,
        alignment=TA_JUSTIFY,
        firstLineIndent=16,
        spaceAfter=0,
    )
    styles.add(body)
    styles.add(
        ParagraphStyle(
            "BodyFirst",
            parent=body,
            firstLineIndent=0,
        )
    )
    styles.add(
        ParagraphStyle(
            "Quote",
            parent=body,
            fontName="Times-Italic",
            leftIndent=18,
            rightIndent=10,
            firstLineIndent=0,
            spaceBefore=6,
            spaceAfter=6,
            textColor=colors.HexColor("#333333"),
        )
    )
    styles.add(
        ParagraphStyle(
            "Bullet",
            parent=body,
            firstLineIndent=0,
            leftIndent=16,
            bulletIndent=2,
            spaceBefore=2,
        )
    )
    styles.add(
        ParagraphStyle(
            "Code",
            fontName="Courier",
            fontSize=9,
            leading=12,
            leftIndent=10,
            spaceBefore=4,
            spaceAfter=6,
        )
    )
    return styles


def _flowables(text: str, styles: StyleSheet1) -> list:
    flow: list = []
    first_paragraph = True

    for block in re.split(r"\n\s*\n", text.strip()):
        stripped = block.strip()
        if not stripped:
            continue
        lines = [line.rstrip() for line in block.splitlines()]
        if _HR.fullmatch(stripped):
            flow.append(
                HRFlowable(
                    width="35%",
                    thickness=0.6,
                    color=colors.HexColor("#999999"),
                    spaceBefore=10,
                    spaceAfter=10,
                    hAlign="CENTER",
                )
            )
            continue

        heading = _HEADING.match(lines[0])
        if heading:
            level = min(len(heading.group(1)), 3)
            flow.append(Paragraph(_inline(heading.group(2)), styles[f"H{level}"]))
            remainder = "\n".join(lines[1:]).strip()
            if remainder:
                flow.extend(_flowables(remainder, styles))
            continue

        if lines[0].lstrip().startswith("```"):
            code_lines = lines[1:]
            if code_lines and code_lines[-1].lstrip().startswith("```"):
                code_lines = code_lines[:-1]
            flow.append(Preformatted("\n".join(code_lines), styles["Code"]))
            continue

        nonempty = [line for line in lines if line.strip()]
        if nonempty and all(line.lstrip().startswith((">",)) for line in nonempty):
            content = " ".join(re.sub(r"^\s*>\s?", "", line) for line in nonempty)
            flow.append(Paragraph(_inline(content), styles["Quote"]))
            first_paragraph = False
            continue

        if nonempty and all(_BULLET.match(line) for line in nonempty):
            for line in nonempty:
                match = _BULLET.match(line)
                flow.append(
                    Paragraph(_inline(match.group(1)), styles["Bullet"], bulletText="•")
                )
            first_paragraph = False
            continue

        content = " ".join(line.strip() for line in nonempty)
        style = styles["BodyFirst"] if first_paragraph else styles["Body"]
        flow.append(Paragraph(_inline(content), style))
        first_paragraph = False

    return flow


class _StoryDoc(BaseDocTemplate):
    def __init__(self, filename: str, **kwargs) -> None:
        super().__init__(
            filename,
            pagesize=A4,
            leftMargin=28 * mm,
            rightMargin=28 * mm,
            topMargin=30 * mm,
            bottomMargin=26 * mm,
            **kwargs,
        )
        frame = Frame(
            self.leftMargin,
            self.bottomMargin,
            self.width,
            self.height,
            id="body",
            leftPadding=0,
            rightPadding=0,
            topPadding=0,
            bottomPadding=0,
        )
        self.addPageTemplates(
            [PageTemplate(id="main", frames=[frame], onPage=self._page_number)]
        )

    def _page_number(self, canvas, doc) -> None:
        if doc.page == 1:
            return
        canvas.saveState()
        canvas.setFont("Times-Roman", 9)
        canvas.setFillColor(colors.HexColor("#666666"))
        canvas.drawCentredString(A4[0] / 2, 14 * mm, str(doc.page - 1))
        canvas.restoreState()


def render_pdf(project: StoryProject, out: Path | None = None) -> Path:
    meta = project.meta
    if out is None:
        out = project.exports_dir / f"{slugify(meta.title)}.pdf"
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)

    styles = _styles()
    doc = _StoryDoc(str(out), title=meta.title, author=meta.author)
    story: list = [Spacer(1, 45 * mm), Paragraph(_inline(meta.title), styles["Title"])]
    if meta.author:
        story.append(Paragraph(f"by {_inline(meta.author)}", styles["Author"]))
    if meta.logline:
        story.append(Paragraph(_inline(meta.logline), styles["Logline"]))
    if meta.genre:
        story.append(Paragraph(_inline(meta.genre), styles["Genre"]))
    story.append(PageBreak())

    chapters = project.chapters()
    for index, chapter in enumerate(chapters):
        story.append(Paragraph(_inline(chapter.title), styles["H1"]))
        story.append(Spacer(1, 4 * mm))
        story.extend(_flowables(chapter.body, styles))
        if index < len(chapters) - 1:
            story.append(PageBreak())

    if not chapters:
        story.append(
            Paragraph("This story has no chapters yet.", styles["BodyFirst"])
        )

    doc.build(story)
    return out
