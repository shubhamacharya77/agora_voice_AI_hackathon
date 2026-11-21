"""Loading the knowledge base into Postgres and searching it (the "R" in RAG).

Search uses PostgreSQL's built-in full-text search: titles weigh most, then section
headings, then the text. Restricted passages are filtered out in the query itself, so
a user who may not read them never gets them, and neither does the model.
"""

from dataclasses import asdict, dataclass
from pathlib import Path

from sqlmodel import col, delete, func, literal_column, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.knowledge.documents import chunk_document, load_documents
from app.models import KnowledgeChunk
from app.search import ENGLISH, all_of, any_of, keywords

__all__ = ["Passage", "ingest", "keywords", "search"]


@dataclass(frozen=True)
class Passage:
    doc_id: str
    doc_title: str
    section: str
    chunk_index: int
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
    """Passages that contain every keyword come first; passages that contain only some
    of them fill any remaining places."""
    words = keywords(query)
    if not words:
        return []
    results = await _ranked(session, all_of(words), include_restricted, limit)
    if len(results) < limit:
        seen = {(p.doc_id, p.chunk_index) for p in results}
        partial = await _ranked(session, any_of(words), include_restricted, limit)
        results += [p for p in partial if (p.doc_id, p.chunk_index) not in seen][: limit - len(results)]
    return results


async def _ranked(
    session: AsyncSession, terms: str, include_restricted: bool, limit: int
) -> list[Passage]:
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
            chunk_index=chunk.chunk_index,
            content=chunk.content,
            restricted=chunk.restricted,
            rank=float(score),
        )
        for chunk, score in rows
    ]
