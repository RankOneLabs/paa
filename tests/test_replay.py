"""Tests for import_events, the archive-replay boundary."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from paa_runtime.events import CURRENT_EVENT_SCHEMA
from paa_runtime.replay import import_events
from paa_runtime.sqlite_store import SqliteEventStore

_TASK = "outbound_content_publish"
_SCOPE = "publish:bluesky"
_SHA = "b" * 64
_EVIDENCE_REF = f"evidence/paa/{_SHA}/evidence.json"


def _make_store(tmp_path: Path) -> SqliteEventStore:
    return SqliteEventStore(tmp_path / "autonomy_events.db")


def _event(**overrides: Any) -> dict[str, Any]:
    """One contract-shaped event, in the exported JSON field names.

    Deliberately built here rather than read from a fixture: import_events
    takes whatever a validated archive contains, so the thing worth pinning
    is that the fields survive the call, not that some particular file exists.
    """
    event: dict[str, Any] = {
        "id": "11111111-1111-4111-8111-111111111111",
        "motion_id": "22222222-2222-4222-8222-222222222222",
        "task": _TASK,
        "declaration_version": 1,
        "scope": _SCOPE,
        "event": "motion_proposed",
        "from_position": "hitl",
        "to_position": "hotl",
        "evidence_ref": _EVIDENCE_REF,
        "evidence_sha256": _SHA,
        "actor": "capture-operator",
        "reason": "imported",
        "created_at": "2026-08-11T19:07:04.074003Z",
        "event_schema": CURRENT_EVENT_SCHEMA,
    }
    event.update(overrides)
    return event


class TestImportEvents:
    def test_preserves_every_field_verbatim(self, tmp_path: Path) -> None:
        # The point of the function: an archive's identifiers and timestamps
        # are the record. Regenerating either would silently rewrite history
        # that another implementation already published.
        store = _make_store(tmp_path)
        events = [
            _event(),
            _event(
                id="33333333-3333-4333-8333-333333333333",
                event="motion_approved",
                created_at="2026-08-11T19:07:04.083086Z",
            ),
        ]
        try:
            import_events(store, events)
            assert [e.to_json_dict() for e in store.get_autonomy_events()] == events
        finally:
            store.close()

    def test_imports_atomically(self, tmp_path: Path) -> None:
        # One transaction, so a bad row late in an archive cannot leave a
        # partial history behind for the next run to append onto.
        store = _make_store(tmp_path)
        duplicate = [_event(), _event(reason="same id as the first")]
        try:
            with pytest.raises(sqlite3.IntegrityError):
                import_events(store, duplicate)
            assert store.get_autonomy_events() == []
        finally:
            store.close()

    def test_importing_nothing_is_a_no_op(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        try:
            import_events(store, [])
            assert store.get_autonomy_events() == []
        finally:
            store.close()
