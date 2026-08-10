"""Tests for SqliteEventStore, the default EventStore implementation."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from paa_runtime.events import CURRENT_EVENT_SCHEMA
from paa_runtime.sqlite_store import SqliteEventStore
from paa_runtime.store import AutonomyEvent

_TASK = "outbound_content_publish"
_SCOPE = "publish:bluesky"
_SHA = "a" * 64
_EVIDENCE_REF = f"evidence/paa/{_SHA}/evidence.json"


def _make_store(tmp_path: Path) -> SqliteEventStore:
    return SqliteEventStore(tmp_path / "autonomy_events.db")


def _event_kwargs(**overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "event_id": "event-1",
        "motion_id": "motion-1",
        "task": _TASK,
        "declaration_version": 1,
        "scope": _SCOPE,
        "event": "motion_proposed",
        "from_position": "manual",
        "to_position": "hitl",
        "evidence_ref": _EVIDENCE_REF,
        "evidence_sha256": _SHA,
        "actor": "steve",
        "reason": "testing",
        "created_at": "2026-08-09T00:00:00.000000Z",
    }
    kwargs.update(overrides)
    return kwargs


class TestInsertAndReadBack:
    def test_field_identical_round_trip(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        kwargs = _event_kwargs()
        with store.transaction():
            store.insert_autonomy_event(**kwargs)

        [event] = store.get_autonomy_events_for_motion("motion-1")
        assert isinstance(event, AutonomyEvent)
        assert event.event_schema == CURRENT_EVENT_SCHEMA
        assert event.id == kwargs["event_id"]
        assert event.motion_id == kwargs["motion_id"]
        assert event.task == kwargs["task"]
        assert event.declaration_version == kwargs["declaration_version"]
        assert event.scope == kwargs["scope"]
        assert event.event == kwargs["event"]
        assert event.from_position == kwargs["from_position"]
        assert event.to_position == kwargs["to_position"]
        assert event.evidence_ref == kwargs["evidence_ref"]
        assert event.evidence_sha256 == kwargs["evidence_sha256"]
        assert event.actor == kwargs["actor"]
        assert event.reason == kwargs["reason"]
        assert event.created_at == kwargs["created_at"]

    def test_custom_event_schema_round_trips(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        kwargs = _event_kwargs(event_schema="paa-autonomy-event/9.9.9-draft")
        with store.transaction():
            store.insert_autonomy_event(**kwargs)

        [event] = store.get_autonomy_events_for_motion("motion-1")
        assert event.event_schema == "paa-autonomy-event/9.9.9-draft"

    def test_to_json_dict_returns_all_fourteen_fields_in_contract_order(
        self, tmp_path: Path
    ) -> None:
        store = _make_store(tmp_path)
        with store.transaction():
            store.insert_autonomy_event(**_event_kwargs())

        [event] = store.get_autonomy_events_for_motion("motion-1")
        assert list(event.to_json_dict().keys()) == [
            "event_schema", "id", "motion_id", "task", "declaration_version",
            "scope", "event", "from_position", "to_position", "evidence_ref",
            "evidence_sha256", "actor", "reason", "created_at",
        ]


class TestNullScope:
    def test_null_scope_round_trips(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        kwargs = _event_kwargs(scope=None, event="position_changed")
        with store.transaction():
            store.insert_autonomy_event(**kwargs)

        [event] = store.get_autonomy_events_for_motion("motion-1")
        assert event.scope is None

    def test_get_latest_position_changed_event_finds_null_scope_row(
        self, tmp_path: Path
    ) -> None:
        store = _make_store(tmp_path)
        kwargs = _event_kwargs(scope=None, event="position_changed")
        with store.transaction():
            store.insert_autonomy_event(**kwargs)

        found = store.get_latest_position_changed_event(
            task=kwargs["task"], declaration_version=kwargs["declaration_version"], scope=None,
        )
        assert found is not None
        assert found.id == kwargs["event_id"]

    def test_get_position_changed_event_before_finds_null_scope_row(
        self, tmp_path: Path
    ) -> None:
        store = _make_store(tmp_path)
        kwargs = _event_kwargs(
            scope=None, event="position_changed", created_at="2026-08-09T00:00:00.000000Z",
        )
        with store.transaction():
            store.insert_autonomy_event(**kwargs)

        found = store.get_position_changed_event_before(
            task=kwargs["task"],
            declaration_version=kwargs["declaration_version"],
            scope=None,
            created_at="2026-08-09T00:00:01.000000Z",
            event_id="zzzz",
        )
        assert found is not None
        assert found.id == kwargs["event_id"]

    def test_non_null_scope_query_does_not_match_null_scope_row(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        kwargs = _event_kwargs(scope=None, event="position_changed")
        with store.transaction():
            store.insert_autonomy_event(**kwargs)

        found = store.get_latest_position_changed_event(
            task=kwargs["task"], declaration_version=kwargs["declaration_version"], scope=_SCOPE,
        )
        assert found is None

    def test_null_scope_query_does_not_match_non_null_scope_row(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        kwargs = _event_kwargs(scope=_SCOPE, event="position_changed")
        with store.transaction():
            store.insert_autonomy_event(**kwargs)

        found = store.get_latest_position_changed_event(
            task=kwargs["task"], declaration_version=kwargs["declaration_version"], scope=None,
        )
        assert found is None


class TestOrdering:
    def test_list_for_motion_orders_ascending_by_created_at_then_id(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        with store.transaction():
            store.insert_autonomy_event(**_event_kwargs(
                event_id="b", motion_id="m1", created_at="2026-08-09T00:00:01.000000Z",
                event="motion_proposed",
            ))
            store.insert_autonomy_event(**_event_kwargs(
                event_id="a", motion_id="m1", created_at="2026-08-09T00:00:00.000000Z",
                event="motion_approved", from_position="hitl", to_position="hitl",
            ))

        events = store.get_autonomy_events_for_motion("m1")
        assert [e.id for e in events] == ["a", "b"]

    def test_get_autonomy_events_unfiltered_orders_ascending(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        with store.transaction():
            store.insert_autonomy_event(**_event_kwargs(
                event_id="later", motion_id="m1", created_at="2026-08-09T00:00:05.000000Z",
            ))
            store.insert_autonomy_event(**_event_kwargs(
                event_id="earlier", motion_id="m2", created_at="2026-08-09T00:00:01.000000Z",
            ))

        events = store.get_autonomy_events()
        assert [e.id for e in events] == ["earlier", "later"]

    def test_get_latest_position_changed_event_orders_descending(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        with store.transaction():
            store.insert_autonomy_event(**_event_kwargs(
                event_id="e1", motion_id="m1", event="position_changed",
                created_at="2026-08-09T00:00:00.000000Z",
            ))
        with store.transaction():
            store.insert_autonomy_event(**_event_kwargs(
                event_id="e2", motion_id="m2", event="position_changed",
                created_at="2026-08-09T00:00:05.000000Z",
            ))

        latest = store.get_latest_position_changed_event(
            task=_TASK, declaration_version=1, scope=_SCOPE,
        )
        assert latest is not None
        assert latest.id == "e2"


class TestPositionChangedEventBeforeTupleComparison:
    def test_same_created_at_uses_id_as_tiebreaker(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        shared_created_at = "2026-08-09T00:00:00.000000Z"
        with store.transaction():
            store.insert_autonomy_event(**_event_kwargs(
                event_id="a", motion_id="m1", event="position_changed",
                created_at=shared_created_at,
            ))
        with store.transaction():
            store.insert_autonomy_event(**_event_kwargs(
                event_id="b", motion_id="m2", event="position_changed",
                created_at=shared_created_at,
            ))

        # Querying with a boundary id greater than both "a" and "b" (same
        # created_at) should return the higher of the two ids, "b".
        before_both = store.get_position_changed_event_before(
            task=_TASK, declaration_version=1, scope=_SCOPE,
            created_at=shared_created_at, event_id="c",
        )
        assert before_both is not None
        assert before_both.id == "b"

        # Querying with boundary id == "b" excludes "b" itself (strict
        # less-than) and falls back to "a" — proving the comparison is a
        # strict tuple boundary, not a created_at-only match.
        before_b = store.get_position_changed_event_before(
            task=_TASK, declaration_version=1, scope=_SCOPE,
            created_at=shared_created_at, event_id="b",
        )
        assert before_b is not None
        assert before_b.id == "a"

        # Querying with boundary id == "a" excludes both.
        before_a = store.get_position_changed_event_before(
            task=_TASK, declaration_version=1, scope=_SCOPE,
            created_at=shared_created_at, event_id="a",
        )
        assert before_a is None

    def test_detects_position_cycled_away_and_back(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)

        # Baseline: task moves manual -> hitl before the motion in question
        # is proposed.
        with store.transaction():
            store.insert_autonomy_event(**_event_kwargs(
                event_id="baseline", motion_id="m0", event="position_changed",
                from_position="manual", to_position="hitl",
                created_at="2026-08-09T00:00:00.000000Z",
            ))

        baseline = store.get_position_changed_event_before(
            task=_TASK, declaration_version=1, scope=_SCOPE,
            created_at="2026-08-09T00:01:00.000000Z", event_id="proposal-event",
        )
        assert baseline is not None
        assert baseline.id == "baseline"

        # Between proposal and approval: an emergency demotion, then a
        # re-promotion back to the same value the baseline already had.
        with store.transaction():
            store.insert_autonomy_event(**_event_kwargs(
                event_id="demoted", motion_id="m1", event="position_changed",
                from_position="hitl", to_position="manual",
                created_at="2026-08-09T00:02:00.000000Z",
            ))
        with store.transaction():
            store.insert_autonomy_event(**_event_kwargs(
                event_id="repromoted", motion_id="m2", event="position_changed",
                from_position="manual", to_position="hitl",
                created_at="2026-08-09T00:03:00.000000Z",
            ))

        current = store.get_latest_position_changed_event(
            task=_TASK, declaration_version=1, scope=_SCOPE,
        )
        assert current is not None
        assert current.id == "repromoted"

        # The value (to_position) is identical to the baseline's, but the
        # identity differs — that is the signal a value-only comparison
        # would miss and the tuple/identity comparison catches.
        assert current.to_position == baseline.to_position == "hitl"
        assert current.id != baseline.id


class TestUniqueConstraint:
    def test_duplicate_event_type_for_same_motion_rejected(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        with store.transaction():
            store.insert_autonomy_event(
                **_event_kwargs(event_id="e1", motion_id="m1", event="motion_proposed")
            )

        with pytest.raises(sqlite3.IntegrityError), store.transaction():
            store.insert_autonomy_event(
                **_event_kwargs(event_id="e2", motion_id="m1", event="motion_proposed")
            )

        # The rejected insert did not partially land.
        events = store.get_autonomy_events_for_motion("m1")
        assert [e.id for e in events] == ["e1"]


class TestAppendOnly:
    def test_update_raises(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        with store.transaction():
            store.insert_autonomy_event(**_event_kwargs())

        with pytest.raises(sqlite3.IntegrityError):
            store._conn.execute(
                "UPDATE autonomy_events SET reason = 'changed' WHERE id = ?", ("event-1",)
            )

        [event] = store.get_autonomy_events_for_motion("motion-1")
        assert event.reason == "testing"

    def test_delete_raises(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        with store.transaction():
            store.insert_autonomy_event(**_event_kwargs())

        with pytest.raises(sqlite3.IntegrityError):
            store._conn.execute("DELETE FROM autonomy_events WHERE id = ?", ("event-1",))

        assert len(store.get_autonomy_events_for_motion("motion-1")) == 1


class TestCheckConstraints:
    def test_invalid_event_type_rejected(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        with pytest.raises(sqlite3.IntegrityError), store.transaction():
            store.insert_autonomy_event(**_event_kwargs(event="not_a_real_event"))

    def test_invalid_from_position_rejected(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        with pytest.raises(sqlite3.IntegrityError), store.transaction():
            store.insert_autonomy_event(**_event_kwargs(from_position="not_a_real_position"))

    def test_invalid_to_position_rejected(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        with pytest.raises(sqlite3.IntegrityError), store.transaction():
            store.insert_autonomy_event(**_event_kwargs(to_position="not_a_real_position"))


class TestTransactionContextManagers:
    def test_transaction_rolls_back_on_exception(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)

        class _Boom(Exception):
            pass

        with pytest.raises(_Boom), store.transaction():
            store.insert_autonomy_event(**_event_kwargs())
            raise _Boom

        assert store.get_autonomy_events_for_motion("motion-1") == []

    def test_begin_immediate_commits_on_clean_exit(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        with store.begin_immediate():
            store.insert_autonomy_event(**_event_kwargs())

        events = store.get_autonomy_events_for_motion("motion-1")
        assert len(events) == 1

    def test_begin_immediate_rolls_back_on_exception(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)

        class _Boom(Exception):
            pass

        with pytest.raises(_Boom), store.begin_immediate():
            store.insert_autonomy_event(**_event_kwargs())
            raise _Boom

        assert store.get_autonomy_events_for_motion("motion-1") == []


class TestGetAutonomyEventsFiltering:
    def test_filters_by_task(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        with store.transaction():
            store.insert_autonomy_event(
                **_event_kwargs(event_id="e1", motion_id="m1", task="task_a")
            )
            store.insert_autonomy_event(**_event_kwargs(
                event_id="e2", motion_id="m2", task="task_b",
                event="motion_approved", from_position="hitl", to_position="hitl",
            ))

        events = store.get_autonomy_events(task="task_a")
        assert [e.id for e in events] == ["e1"]

    def test_unfiltered_returns_everything(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        with store.transaction():
            store.insert_autonomy_event(**_event_kwargs(event_id="e1", motion_id="m1"))
            store.insert_autonomy_event(**_event_kwargs(
                event_id="e2", motion_id="m2",
                event="motion_approved", from_position="hitl", to_position="hitl",
            ))

        events = store.get_autonomy_events()
        assert {e.id for e in events} == {"e1", "e2"}


class TestTransactionRequired:
    """Semantic 1 of the EventStore protocol is enforced, not documented."""

    def test_insert_outside_a_transaction_is_refused(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        with pytest.raises(RuntimeError, match="requires an open transaction"):
            store.insert_autonomy_event(**_event_kwargs())

    def test_refused_insert_writes_nothing(self, tmp_path: Path) -> None:
        """The point of the check is that the orphan write never lands.

        Without it sqlite3 opens an implicit transaction and commits the
        row on its own schedule, so an event meant to be half of an
        atomic pair is durable on its own.
        """
        store = _make_store(tmp_path)
        with pytest.raises(RuntimeError):
            store.insert_autonomy_event(**_event_kwargs())
        assert store.get_autonomy_events() == []

    def test_insert_inside_begin_immediate_is_allowed(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        with store.begin_immediate():
            store.insert_autonomy_event(**_event_kwargs())
        assert len(store.get_autonomy_events()) == 1
