import uuid

from sqlmodel import Field, Text, UniqueConstraint

from app.models.base import CreatedAtMixin


class KnowledgeChunk(CreatedAtMixin, table=True):
    """One searchable passage of a company document: the knowledge base behind RAG."""

    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("doc_id", "chunk_index", name="uq_knowledge_chunks_doc_id_chunk_index"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    doc_id: str = Field(max_length=100)  # the file name, e.g. "leave-and-holidays"
    doc_title: str = Field(max_length=200)
    category: str = Field(max_length=50)
    section: str = Field(max_length=200)  # the "## " heading this passage sits under
    chunk_index: int
    content: str = Field(sa_type=Text)
    # Admins only: readable with the knowledge:read_restricted permission.
    restricted: bool = Field(default=False)
