"""Sample data behind the remaining demo tools (meetings and inventory).

Employees and tickets live in PostgreSQL (see app/company.py).

Dates are relative to today, so the demo always looks current. Company policies and
documents live in the knowledge base (backend/knowledge/alrium), not here. Addresses use
the reserved `.example` domain, so nothing can ever reach a real mailbox.
"""

import re
from datetime import date, datetime, time, timedelta
from typing import Any


def now() -> datetime:
    return datetime.now().astimezone()


def today() -> date:
    return now().date()


def _at(day: date, hour: int, minute: int) -> str:
    return datetime.combine(day, time(hour, minute)).isoformat(timespec="minutes")


def _ago(**delta: int) -> str:
    return (now() - timedelta(**delta)).isoformat(timespec="minutes")


# --- mail ---

EMAIL_ADDRESS = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# --- meetings ---


def meetings() -> list[dict[str, Any]]:
    d0 = today()
    d1, d2 = d0 + timedelta(days=1), d0 + timedelta(days=2)
    return [
        {
            "id": "mtg-101",
            "title": "Deployment status check",
            "start": _at(d0, 10, 0),
            "end": _at(d0, 10, 30),
            "attendees": ["Akshay Rao", "Neha Kulkarni"],
            "location": "Room 2B",
        },
        {
            "id": "mtg-102",
            "title": "Q3 budget review",
            "start": _at(d0, 15, 0),
            "end": _at(d0, 16, 0),
            "attendees": ["Priya Nair", "Neha Kulkarni"],
            "location": "Board room",
        },
        {
            "id": "mtg-103",
            "title": "Cleaning vendor call",
            "start": _at(d1, 11, 0),
            "end": _at(d1, 11, 30),
            "attendees": ["Facilities Team", "Neha Kulkarni"],
            "location": "Phone",
        },
        {
            "id": "mtg-104",
            "title": "Team weekly sync",
            "start": _at(d2, 9, 30),
            "end": _at(d2, 10, 0),
            "attendees": ["Engineering team", "Neha Kulkarni"],
            "location": "Room 1A",
        },
    ]


# --- facilities and inventory ---

INVENTORY: list[dict[str, Any]] = [
    {"item": "Printer paper (A4 reams)", "quantity": 12, "reorder_at": 20, "location": "Supply room"},
    {"item": "Whiteboard markers", "quantity": 40, "reorder_at": 25, "location": "Supply room"},
    {"item": "Coffee beans (kg)", "quantity": 3, "reorder_at": 5, "location": "Pantry"},
    {"item": "USB-C laptop chargers", "quantity": 6, "reorder_at": 4, "location": "IT cupboard"},
    {"item": "First-aid kits", "quantity": 2, "reorder_at": 2, "location": "Reception"},
]
