"""Shared helpers for PostgreSQL full-text search (knowledge base and conversation recall)."""

import re

from sqlmodel import literal_column

ENGLISH = literal_column("'english'::regconfig")


def keywords(text: str) -> list[str]:
    """'How many leave days?' -> ['how', 'many', 'leave', 'days'].

    Only letters and digits get through, so nothing can inject tsquery syntax. Postgres
    drops stop words ("how", "is") itself when it builds the query.
    """
    words = [w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 1]
    return list(dict.fromkeys(words))


def all_of(words: list[str]) -> str:
    return " & ".join(words)


def any_of(words: list[str]) -> str:
    return " | ".join(words)
