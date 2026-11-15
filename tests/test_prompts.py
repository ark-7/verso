from verso.models import POV, Character, OutlineEntry, StoryMeta, WritingMode
from verso.prompts import (
    REVIEWER_SYSTEM,
    SCREENWRITER_SYSTEM,
    chapter_prompt,
    outline_prompt,
    parse_outline,
    parse_suggestions,
    review_prompt,
    story_bible,
)


def test_parse_outline_variants():
    text = """Here is your outline:

1. The Ferryman's Debt — Mara learns the river takes more than coin.
2. **The Crossing** - A storm forces her to choose.
3. The Shore: Dawn brings a stranger.
"""
    entries = parse_outline(text)

    assert [entry.title for entry in entries] == [
        "The Ferryman's Debt",
        "The Crossing",
        "The Shore",
    ]
    assert entries[0].summary == "Mara learns the river takes more than coin."
    assert entries[1].summary == "A storm forces her to choose."
    assert entries[2].summary == "Dawn brings a stranger."


def test_parse_outline_without_summary():
    entries = parse_outline("1. The Storm\n2. The Shore")
    assert entries[0].title == "The Storm"
    assert entries[0].summary == ""


def test_parse_outline_ignores_prose():
    assert parse_outline("No numbered beats here.") == []


def test_story_bible_contents():
    meta = StoryMeta(
        title="The Ferryman",
        genre="Noir",
        tone="Bleak",
        pov=POV.first,
        characters=[
            Character(name="Mara", role="protagonist", description="A ferryman.")
        ],
        outline=[OutlineEntry(title="The Storm", summary="A crossing goes wrong.")],
    )
    bible = story_bible(meta)

    assert "First person" in bible
    assert "Mara (protagonist): A ferryman." in bible
    assert "1. The Storm — A crossing goes wrong." in bible
    assert "The Storm" not in story_bible(meta, include_outline=False)


def test_story_bible_includes_interactive_preferences():
    meta = StoryMeta(
        title="The Ferryman",
        opening="A storm strands Mara on the far bank.",
        ending="She pays the debt with her own name.",
        references=["The Odyssey", "Dune"],
        notes="The river is never explained.",
    )

    bible = story_bible(meta)

    assert "Opening: A storm strands Mara on the far bank." in bible
    assert "Intended ending: She pays the debt with her own name." in bible
    assert "References and influences: The Odyssey; Dune" in bible
    assert "Story notes: The river is never explained." in bible


def test_story_bible_includes_user_direction_for_a_chapter():
    meta = StoryMeta(
        title="The Ferryman",
        outline=[
            OutlineEntry(
                title="The Storm",
                summary="A crossing goes wrong.",
                direction="The ferry capsizes.",
            )
        ],
    )

    assert "User direction: The ferry capsizes." in story_bible(meta)
    replanning = story_bible(meta, include_outline=False)
    assert "User directions from the existing plan:" in replanning
    assert "The ferry capsizes." in replanning
    assert "A crossing goes wrong." not in replanning


def test_chapter_prompt_puts_user_direction_first():
    meta = StoryMeta(title="The Ferryman")
    entry = OutlineEntry(
        title="The Storm",
        summary="A crossing goes wrong.",
        direction="Mara burns the ledger instead of delivering it.",
    )

    prompt = chapter_prompt(meta, 3, entry)

    assert "Mara burns the ledger instead of delivering it." in prompt
    assert "takes precedence" in prompt
    assert prompt.index("takes precedence") < prompt.index("Target length")


def test_chapter_prompt_repeats_the_requested_opening_and_ending():
    meta = StoryMeta(
        title="The Ferryman",
        opening="A storm strands Mara.",
        ending="She pays with her name.",
        outline=[
            OutlineEntry(title="The Storm"),
            OutlineEntry(title="The Shore"),
        ],
    )

    first = chapter_prompt(meta, 1, meta.outline[0])
    last = chapter_prompt(meta, 2, meta.outline[1])

    assert "Requested opening: A storm strands Mara." in first
    assert "Requested ending for the final chapter" not in first
    assert "Requested ending for the final chapter: She pays with her name." in last


def test_outline_prompt_requires_the_requested_ending():
    meta = StoryMeta(
        title="The Ferryman",
        opening="A storm.",
        ending="She pays with her name.",
    )

    prompt = outline_prompt(meta, 6)

    assert "must realize the opening" in prompt
    assert "must land the intended ending" in prompt


def test_screenplay_mode_switches_the_bible_and_chapter_prompt():
    meta = StoryMeta(
        title="The Ferryman",
        mode=WritingMode.screenplay,
        outline=[OutlineEntry(title="Cold Open")],
    )

    bible = story_bible(meta)
    prompt = chapter_prompt(meta, 1, meta.outline[0], words=300)

    assert "Format: screenplay" in bible
    assert "Fountain" in prompt
    assert "scene heading" in prompt
    assert "2 screenplay page(s)" in prompt
    assert "Fountain" in SCREENWRITER_SYSTEM
    assert "Never use Markdown" in SCREENWRITER_SYSTEM


def test_outline_prompt_notes_screenplay_sequences():
    meta = StoryMeta(title="The Ferryman", mode=WritingMode.screenplay)

    prompt = outline_prompt(meta, 4)

    assert "sequence of 2–4 scenes" in prompt


def test_chapter_prompt_links_to_the_previous_chapter():
    meta = StoryMeta(title="The Ferryman")
    prompt = chapter_prompt(
        meta,
        2,
        OutlineEntry(title="The Shore"),
        previous_tail="Mara rowed into the dark.",
        previous_number=1,
        previous_title="The Storm",
        previous_summary="A crossing goes wrong.",
    )

    assert "Chapter 1 was: The Storm — A crossing goes wrong." in prompt
    assert "Mara rowed into the dark." in prompt
    assert "direct continuity" in prompt
    assert "do not recap" in prompt


def test_story_bible_includes_only_author_approved_suggestions():
    meta = StoryMeta(
        title="The Ferryman",
        accepted_suggestions=["Give Mara a physical tell.", "Let the river answer once."],
    )

    bible = story_bible(meta)

    assert "Author-approved suggestions" in bible
    assert "Give Mara a physical tell." in bible
    assert "Let the river answer once." in bible


def test_review_prompt_keeps_the_author_primary():
    meta = StoryMeta(
        title="The Ferryman",
        logline="A ferryman discovers the river is collecting debts.",
    )

    prompt = review_prompt(meta, "Mara burns the ledger instead of delivering it.")

    assert "Mara burns the ledger instead of delivering it." in prompt
    assert "author's idea is primary" in prompt.lower()
    assert "What works" in prompt
    assert "author's idea is primary" in REVIEWER_SYSTEM.lower()
    assert "optional" in REVIEWER_SYSTEM.lower()


def test_parse_suggestions_extracts_numbered_options():
    text = """What works
The debt premise is strong.

Questions
1. Why does Mara stay?

Optional suggestions
1. Give Mara a physical tell.
2. Let the river answer once.
"""
    assert parse_suggestions(text) == [
        "Give Mara a physical tell.",
        "Let the river answer once.",
    ]


def test_parse_suggestions_returns_nothing_without_a_suggestions_section():
    assert parse_suggestions("What works\n1. Everything.\n") == []
