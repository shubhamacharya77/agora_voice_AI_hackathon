"""The Alrium knowledge base: documents, chunking, loading, search and permissions (RAG)."""

import json
import uuid

import pytest

from app.agents.llm import ToolCall
from app.agents.specialists import build_registry
from app.agents.tools import ToolContext, UserContext
from app.auth.permissions import permissions_for
from app.config import get_settings
from app.db import Database
from app.knowledge import store as knowledge
from app.knowledge.documents import MAX_CHUNK_CHARS, Document, chunk_document, load_documents
from app.models import Role

KNOWLEDGE_DIR = get_settings().knowledge_dir


# --- documents (no database) ---


def test_every_document_loads_with_valid_front_matter() -> None:
    documents = load_documents(KNOWLEDGE_DIR)

    assert len(documents) >= 10
    assert all(d.title and d.category for d in documents)
    restricted = {d.doc_id for d in documents if d.restricted}
    assert restricted == {"security-incident-response", "vendors-and-contracts"}


def test_documents_split_into_one_passage_per_section() -> None:
    document = Document(
        doc_id="sample",
        title="Sample",
        category="Test",
        restricted=False,
        body="# Sample\n\nIntro line.\n\n## First\n\nAlpha.\n\n## Second\n\nBeta.",
    )

    chunks = chunk_document(document)

    assert [(c.section, c.content) for c in chunks] == [
        ("Sample", "Intro line."),
        ("First", "Alpha."),
        ("Second", "Beta."),
    ]
    assert [c.chunk_index for c in chunks] == [0, 1, 2]


def test_no_passage_is_too_long() -> None:
    chunks = [c for d in load_documents(KNOWLEDGE_DIR) for c in chunk_document(d)]

    assert all(len(c.content) <= MAX_CHUNK_CHARS for c in chunks)


def test_search_terms_are_sanitised() -> None:
    assert knowledge.keywords("How many leave days?") == ["how", "many", "leave", "days"]
    assert knowledge.keywords("drop table; -- & | !") == ["drop", "table"]
    assert knowledge.keywords("?!") == []


# --- loading and search (test database) ---


@pytest.fixture
def loaded(run_db) -> int:
    async def _load(db: Database) -> int:
        async with db.sessionmaker() as session:
            count = await knowledge.ingest(session, KNOWLEDGE_DIR)
            await session.commit()
            return count

    return run_db(_load)


def _search(run_db, query: str, *, include_restricted: bool) -> list[knowledge.Passage]:
    async def _go(db: Database) -> list[knowledge.Passage]:
        async with db.sessionmaker() as session:
            return await knowledge.search(session, query, include_restricted=include_restricted)

    return run_db(_go)


@pytest.mark.integration
def test_reloading_replaces_rather_than_duplicates(loaded, run_db) -> None:
    async def _reload(db: Database) -> int:
        async with db.sessionmaker() as session:
            count = await knowledge.ingest(session, KNOWLEDGE_DIR)
            await session.commit()
            return count

    assert run_db(_reload) == loaded


@pytest.mark.integration
@pytest.mark.parametrize(
    ("question", "expected_document"),
    [
        ("How many days of annual leave do I get?", "Leave and holidays"),
        ("Can I work from home?", "Working hours and remote work"),
        ("What is the hotel limit when travelling?", "Expenses and travel"),
        ("When is salary paid?", "Payroll, benefits and reviews"),
        ("I got a suspicious phishing email", "IT and security"),
        ("Who is the CEO?", "About Alrium"),
        ("How do I book a meeting room?", "Offices and facilities"),
    ],
)
def test_search_finds_the_right_document(loaded, run_db, question, expected_document) -> None:
    passages = _search(run_db, question, include_restricted=False)

    assert passages, f"nothing found for {question!r}"
    assert passages[0].doc_title == expected_document


@pytest.mark.integration
def test_admin_only_documents_never_reach_employees(loaded, run_db) -> None:
    question = "security incident response severity CERT-In vendor contracts renewal"

    employee = _search(run_db, question, include_restricted=False)
    admin = _search(run_db, question, include_restricted=True)

    assert not any(p.restricted for p in employee)
    assert any(p.restricted for p in admin)


@pytest.mark.integration
def test_knowledge_tool_respects_the_users_permissions(loaded, run_db) -> None:
    registry = build_registry()

    async def _ask(db: Database, role: Role) -> dict:
        user = UserContext(
            id=uuid.uuid4(), name="T", email="t@coo.example", role=role, permissions=permissions_for(role)
        )
        async with db.sessionmaker() as session:
            context = ToolContext(user=user, conversation_id=uuid.uuid4(), session=session)
            call = ToolCall("toolu_1", "company_search_knowledge", {"query": "vendor contracts renewal"})
            result = await registry.execute(call, context)
        return json.loads(result.content)

    employee = run_db(lambda db: _ask(db, Role.EMPLOYEE))
    admin = run_db(lambda db: _ask(db, Role.ADMIN))

    assert "Vendors and contracts (admins)" not in {p["document"] for p in employee["passages"]}
    assert "Vendors and contracts (admins)" in {p["document"] for p in admin["passages"]}
    assert admin["mock"] is True
