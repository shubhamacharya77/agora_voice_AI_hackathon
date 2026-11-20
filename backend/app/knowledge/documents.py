"""Reading knowledge-base markdown files and splitting them into passages (no database)."""

import re
from dataclasses import dataclass
from pathlib import Path

FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
MAX_CHUNK_CHARS = 1500
AUDIENCES = {"everyone": False, "admins": True}  # audience -> restricted


@dataclass(frozen=True)
class Document:
    doc_id: str
    title: str
    category: str
    restricted: bool
    body: str


@dataclass(frozen=True)
class Chunk:
    doc_id: str
    doc_title: str
    category: str
    section: str
    chunk_index: int
    content: str
    restricted: bool


def parse_document(path: Path) -> Document:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    match = FRONT_MATTER.match(text)
    if match is None:
        raise ValueError(f"{path.name}: missing front matter")
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, separator, value = line.partition(":")
        if separator:
            meta[key.strip()] = value.strip()
    missing = [key for key in ("title", "category") if not meta.get(key)]
    if missing:
        raise ValueError(f"{path.name}: front matter needs {', '.join(missing)}")
    audience = meta.get("audience", "everyone")
    if audience not in AUDIENCES:
        raise ValueError(f"{path.name}: audience must be 'everyone' or 'admins', not {audience!r}")
    return Document(
        doc_id=path.stem,
        title=meta["title"],
        category=meta["category"],
        restricted=AUDIENCES[audience],
        body=text[match.end() :].strip(),
    )


def load_documents(directory: Path) -> list[Document]:
    """Every markdown document in the folder, except its README."""
    return [
        parse_document(path)
        for path in sorted(directory.glob("*.md"))
        if path.name.lower() != "readme.md"
    ]


def _split(text: str) -> list[str]:
    """Keep a section whole when it's short enough; otherwise split between paragraphs."""
    if len(text) <= MAX_CHUNK_CHARS:
        return [text]
    pieces: list[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        if current and len(current) + len(paragraph) + 2 > MAX_CHUNK_CHARS:
            pieces.append(current)
            current = paragraph
        else:
            current = f"{current}\n\n{paragraph}" if current else paragraph
    if current:
        pieces.append(current)
    return pieces


def chunk_document(document: Document) -> list[Chunk]:
    """One passage per `## ` section (the intro counts as a section named after the title)."""
    sections: list[tuple[str, list[str]]] = [(document.title, [])]
    for line in document.body.splitlines():
        if line.startswith("## "):
            sections.append((line[3:].strip(), []))
        elif line.startswith("# "):
            continue  # the document title, already known
        else:
            sections[-1][1].append(line)

    chunks: list[Chunk] = []
    for heading, lines in sections:
        text = "\n".join(lines).strip()
        if not text:
            continue
        for piece in _split(text):
            chunks.append(
                Chunk(
                    doc_id=document.doc_id,
                    doc_title=document.title,
                    category=document.category,
                    section=heading,
                    chunk_index=len(chunks),
                    content=piece,
                    restricted=document.restricted,
                )
            )
    return chunks
