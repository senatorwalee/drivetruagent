"""Reading knowledge documents and cutting them into chunks. No database."""

import json
from pathlib import Path

import pytest

from drivetruagent.enums import KnowledgeAudience, KnowledgeType
from drivetruagent.knowledge.documents import (
    InvalidDocument,
    SourceDocument,
    chunk_sections,
    parse_document,
)

ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE = ROOT / "knowledge"

FRONT = """+++
id = "fryer"
title = "Fryer oil"
type = "preparation"
audience = "customer"
items = ["fries"]
version = "2026-10-01"
+++
"""


def document(body: str = "## Oil\n\nCanola.\n", front: str = FRONT) -> str:
    return front + body


# --- Parsing ----------------------------------------------------------------


def test_the_front_matter_is_read() -> None:
    parsed = parse_document(document())

    meta = parsed.meta
    assert (meta.id, meta.title, meta.type, meta.audience, meta.items) == (
        "fryer",
        "Fryer oil",
        KnowledgeType.PREPARATION,
        KnowledgeAudience.CUSTOMER,
        ["fries"],
    )


def test_each_section_is_a_chunk_that_keeps_the_title_and_heading() -> None:
    parsed = parse_document(
        document(
            "# Fryer oil\n\nIntro.\n\n"
            "## What oil\n\nCanola.\n\n"
            "## How often\n\nDaily.\n"
        )
    )

    assert [(c.position, c.heading) for c in parsed.chunks] == [
        (0, "What oil"),
        (1, "How often"),
    ]
    assert parsed.chunks[0].text == "Fryer oil · What oil\n\nCanola."


def test_text_before_the_first_section_is_not_a_chunk() -> None:
    chunks = chunk_sections("T", "Intro words.\n## A\nBody.")

    assert [c.heading for c in chunks] == ["A"]
    assert "Intro" not in chunks[0].text


def test_a_section_with_no_text_is_skipped() -> None:
    chunks = chunk_sections("T", "## Empty\n\n## Full\nBody.")

    assert [(c.position, c.heading) for c in chunks] == [(0, "Full")]


def test_a_deeper_heading_stays_inside_its_section() -> None:
    (chunk,) = chunk_sections("T", "## A\nOne.\n### Detail\nTwo.")

    assert chunk.text.endswith("One.\n### Detail\nTwo.")


def test_any_change_changes_the_hashes() -> None:
    before = parse_document(document("## Oil\n\nCanola.\n"))
    after = parse_document(document("## Oil\n\nSunflower.\n"))

    assert before.content_hash != after.content_hash
    assert before.chunks[0].content_hash != after.chunks[0].content_hash


def test_the_same_text_hashes_the_same() -> None:
    assert parse_document(document()).chunks == parse_document(document()).chunks


# --- What is refused --------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "complaint"),
    [
        ("## Oil\n\nCanola.\n", "must start"),
        ("+++\nid = 'x'\n## Oil\nCanola.\n", "not closed"),
        (document(front="+++\nid = \n+++\n"), "front matter"),
        (document(front=FRONT.replace('"preparation"', '"gossip"')), "front matter"),
        (document(front=FRONT.replace('"customer"', '"public"')), "front matter"),
        (
            document(front=FRONT.replace("+++\n", '+++\nsecret = "x"\n', 1)),
            "front matter",
        ),
        (
            document(front=FRONT.replace('id = "fryer"', 'id = "Fryer Oil"')),
            "front matter",
        ),
        (document("No sections here.\n"), "no '## ' sections"),
        (document("## Oil\nA.\n## Oil\nB.\n"), "same heading"),
    ],
)
def test_a_bad_document_is_refused_with_a_reason(source: str, complaint: str) -> None:
    with pytest.raises(InvalidDocument, match=complaint):
        parse_document(source, name="bad.md")


def test_the_file_name_is_in_the_error() -> None:
    with pytest.raises(InvalidDocument, match=r"^oops\.md:"):
        parse_document("nothing", name="oops.md")


# --- The real documents -----------------------------------------------------


def real_documents() -> list[SourceDocument]:
    return [
        parse_document(path.read_text(encoding="utf-8"), name=path.name)
        for path in sorted(KNOWLEDGE.glob("*.md"))
    ]


def test_every_real_document_is_valid_with_a_unique_id() -> None:
    documents = real_documents()

    ids = [d.meta.id for d in documents]
    assert len(ids) == len(set(ids)) >= 5


def test_the_real_documents_name_only_items_on_the_menu() -> None:
    menu = json.loads((ROOT / "data" / "menu.json").read_text(encoding="utf-8"))
    item_codes = {item["code"] for c in menu["categories"] for item in c["items"]}

    named = {code for d in real_documents() for code in d.meta.items}
    assert named <= item_codes


def test_some_real_documents_are_for_staff_only() -> None:
    audiences = {d.meta.id: d.meta.audience for d in real_documents()}

    assert audiences["complaint-resolution"] is KnowledgeAudience.STAFF
    assert audiences["fryer-and-oil"] is KnowledgeAudience.CUSTOMER
