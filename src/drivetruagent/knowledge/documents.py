"""Reading approved knowledge documents and cutting them into chunks.

Pure logic: text in, plain values out. No database, no model.

A document is Markdown with TOML front matter between `+++` lines. It is cut
at each `##` heading: one section per chunk, so a chunk is about one thing.
Each chunk's text starts with the document's title and its heading, so a
section like "What oil we use" keeps saying what it is about once it is
separated from the rest.
"""

import hashlib
import tomllib
from dataclasses import dataclass
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from drivetruagent.enums import KnowledgeAudience, KnowledgeType

FRONT_MATTER = "+++"

Code = Annotated[str, Field(pattern=r"^[a-z0-9_-]+$")]


class InvalidDocument(ValueError):
    """A knowledge file that cannot be used, and why."""


class DocumentMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: Code
    title: Annotated[str, Field(min_length=1)]
    type: KnowledgeType
    audience: KnowledgeAudience
    # Menu item codes the document is about. Empty: it applies to everything.
    items: list[Code] = Field(default_factory=list)
    # When it was approved, or any label that changes with each approval.
    version: Annotated[str, Field(min_length=1)]


@dataclass(frozen=True)
class Chunk:
    position: int
    heading: str
    # What is embedded and what the agent reads: title, heading and body.
    text: str
    content_hash: str


@dataclass(frozen=True)
class SourceDocument:
    meta: DocumentMeta
    chunks: tuple[Chunk, ...]
    # Of the whole file, front matter included: any change shows.
    content_hash: str


def parse_document(source: str, *, name: str = "document") -> SourceDocument:
    """Read one file's text. Raises InvalidDocument with what is wrong."""
    front, body = _split_front_matter(source, name)
    try:
        meta = DocumentMeta.model_validate(tomllib.loads(front))
    except (tomllib.TOMLDecodeError, ValidationError) as error:
        raise InvalidDocument(f"{name}: front matter: {error}") from error

    chunks = chunk_sections(meta.title, body)
    if not chunks:
        raise InvalidDocument(f"{name}: no '## ' sections with text under them")
    headings = [chunk.heading for chunk in chunks]
    if len(set(headings)) != len(headings):
        raise InvalidDocument(f"{name}: two sections have the same heading")
    return SourceDocument(meta, chunks, _hash(source))


def chunk_sections(title: str, body: str) -> tuple[Chunk, ...]:
    """One chunk per `##` section with text in it. Text before the first
    section (such as a `#` title line) is not a chunk."""
    sections: list[tuple[str, list[str]]] = []
    for line in body.splitlines():
        if line.startswith("## "):
            sections.append((line[3:].strip(), []))
        elif sections:
            sections[-1][1].append(line)

    chunks: list[Chunk] = []
    for heading, lines in sections:
        text = "\n".join(lines).strip()
        if not heading or not text:
            continue
        full = f"{title} · {heading}\n\n{text}"
        chunks.append(Chunk(len(chunks), heading, full, _hash(full)))
    return tuple(chunks)


def _split_front_matter(source: str, name: str) -> tuple[str, str]:
    lines = source.splitlines()
    if not lines or lines[0].strip() != FRONT_MATTER:
        raise InvalidDocument(f"{name}: must start with a '{FRONT_MATTER}' line")
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == FRONT_MATTER:
            return "\n".join(lines[1:index]), "\n".join(lines[index + 1 :])
    raise InvalidDocument(f"{name}: front matter is not closed with '{FRONT_MATTER}'")


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
