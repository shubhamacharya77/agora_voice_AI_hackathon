"""Loading the knowledge base into Postgres and searching it (the "R" in RAG).

Search uses PostgreSQL's built-in full-text search: titles weigh most, then section
headings, then the text. Restricted passages are filtered out in the query itself, so
a user who may not read them never gets them, and neither does the model.
"""

import re
from dataclasses import asdict, dataclass
from pathlib import Path

from sqlmodel import col, delete, func, literal_column, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.knowledge.documents import chunk_document, load_documents
from app.models import KnowledgeChunk

ENGLISH = literal_column("'english'::regconfig")


@dataclass(frozen=True)
class Passage:
    doc_id: str
    doc_title: str
    section: str
    content: str
    restricted: bool
    rank: float


async def ingest(session: AsyncSession, directory: Path) -> int:
    """Replace the whole knowledge base with the documents in `directory`. Flushes, doesn't commit."""
    chunks = [chunk for document in load_documents(directory) for chunk in chunk_document(document)]
    await session.exec(delete(KnowledgeChunk))
    session.add_all([KnowledgeChunk(**asdict(chunk)) for chunk in chunks])
    await session.flush()
    return len(chunks)


def or_query(text: str) -> str | None:
    """'How many leave days?' -> 'how | many | leave | days': any word can match,
    and passages matching more words rank higher. Only letters and digits get through."""
    words = [w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 1]
    return " | ".join(dict.fromkeys(words)) or None


def _document_vector():
    weighted = [
        func.setweight(func.to_tsvector(ENGLISH, col(KnowledgeChunk.doc_title)), literal_column("'A'")),
        func.setweight(func.to_tsvector(ENGLISH, col(KnowledgeChunk.section)), literal_column("'B'")),
        func.setweight(func.to_tsvector(ENGLISH, col(KnowledgeChunk.content)), literal_column("'C'")),
    ]
    vector = weighted[0]
    for part in weighted[1:]:
        vector = vector.op("||")(part)
    return vector


async def search(
    session: AsyncSession, query: str, *, include_restricted: bool, limit: int = 4
) -> list[Passage]:
    terms = or_query(query)
    if terms is None:
        return []
    vector = _document_vector()
    tsquery = func.to_tsquery(ENGLISH, terms)
    rank = func.ts_rank_cd(vector, tsquery).label("rank")
    statement = select(KnowledgeChunk, rank).where(vector.bool_op("@@")(tsquery))
    if not include_restricted:
        statement = statement.where(col(KnowledgeChunk.restricted).is_(False))
    statement = statement.order_by(
        rank.desc(), col(KnowledgeChunk.doc_id), col(KnowledgeChunk.chunk_index)
    ).limit(limit)
    rows = (await session.exec(statement)).all()
    return [
        Passage(
            doc_id=chunk.doc_id,
            doc_title=chunk.doc_title,
            section=chunk.section,
            content=chunk.content,
            restricted=chunk.restricted,
            rank=float(score),
        )
        for chunk, score in rows
    ]
