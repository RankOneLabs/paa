"""The default ``EventStore``: one SQLite ``autonomy_events`` table.

One table, shaped by the published ``paa-autonomy-event`` contract:

- ``event_schema TEXT NOT NULL`` — every row stamps the schema version it
  conforms to, so a stored row and a published fixture are the same shape.
- ``scope`` is nullable. A task that declares no ``scopes`` has a null
  scope in the contract, which is the case for most published example
  tasks — a NOT NULL column could not store them.
- CHECK constraints pin ``event``, ``from_position``, and ``to_position``
  to their declared vocabularies at the storage layer.
- Two indexes serve the two access patterns (by scope-triple, by motion),
  and a ``(motion_id, event)`` unique index enforces at most one event of
  each type per motion.
- Two triggers abort UPDATE and DELETE outright: append-only is a storage
  invariant here, not a convention callers are trusted to honor.

Nullable ``scope`` has one sharp edge: in SQLite, ``scope = ?`` never
matches NULL. The two scope-filtered readers (``get_latest_position_
changed_event`` and ``get_position_changed_event_before``) use
``scope IS ?`` instead — get this wrong and every null-scope task
silently resolves to its initial position forever, since the lookup for
its position_changed events would never match.

This store owns its own ``sqlite3`` connection and creates its schema on
construction if absent — the runtime owns this schema and does not
participate in any consumer's migration numbering. ``transaction()`` and
``begin_immediate()`` are intentionally not reentrant (``EventStore``
semantic 5: transaction contexts do not nest in any runtime code path);
attempting to nest one raises ``sqlite3.OperationalError`` from the
underlying ``BEGIN``. A store that needed reentrancy would have to track
depth and issue savepoints; this one does not, which is why it stays
small.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from paa_runtime.events import CURRENT_EVENT_SCHEMA, AutonomyEventType, AutonomyPosition
from paa_runtime.store import AutonomyEvent, EventStore

__all__ = ["SqliteEventStore"]

_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS autonomy_events (
    event_schema        TEXT NOT NULL,
    id                  TEXT PRIMARY KEY,
    motion_id           TEXT NOT NULL,
    task                TEXT NOT NULL,
    declaration_version INTEGER NOT NULL,
    scope               TEXT,
    event               TEXT NOT NULL CHECK(event IN (
        'motion_proposed', 'motion_approved', 'motion_rejected', 'position_changed'
    )),
    from_position       TEXT NOT NULL CHECK(from_position IN (
        'manual', 'hitl', 'hotl', 'autonomous'
    )),
    to_position         TEXT NOT NULL CHECK(to_position IN (
        'manual', 'hitl', 'hotl', 'autonomous'
    )),
    evidence_ref        TEXT NOT NULL,
    evidence_sha256     TEXT NOT NULL,
    actor               TEXT NOT NULL,
    reason              TEXT NOT NULL,
    created_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS autonomy_events_scope_idx
    ON autonomy_events(task, declaration_version, scope, created_at, id);

CREATE INDEX IF NOT EXISTS autonomy_events_motion_idx
    ON autonomy_events(motion_id, created_at, id);

CREATE UNIQUE INDEX IF NOT EXISTS autonomy_events_motion_event_unique
    ON autonomy_events(motion_id, event);

CREATE TRIGGER IF NOT EXISTS autonomy_events_no_update
BEFORE UPDATE ON autonomy_events
BEGIN
    SELECT RAISE(ABORT, 'autonomy_events is append-only');
END;

CREATE TRIGGER IF NOT EXISTS autonomy_events_no_delete
BEFORE DELETE ON autonomy_events
BEGIN
    SELECT RAISE(ABORT, 'autonomy_events is append-only');
END;
"""


def _row_to_event(row: sqlite3.Row) -> AutonomyEvent:
    return AutonomyEvent(
        event_schema=row["event_schema"],
        id=row["id"],
        motion_id=row["motion_id"],
        task=row["task"],
        declaration_version=row["declaration_version"],
        scope=row["scope"],
        event=row["event"],
        from_position=row["from_position"],
        to_position=row["to_position"],
        evidence_ref=row["evidence_ref"],
        evidence_sha256=row["evidence_sha256"],
        actor=row["actor"],
        reason=row["reason"],
        created_at=row["created_at"],
    )


class SqliteEventStore:
    """Default ``EventStore`` implementation over one SQLite database.

    Owns exactly one ``sqlite3`` connection for the lifetime of the
    instance. Creates the ``autonomy_events`` table, its indexes, and its
    append-only triggers on construction if they don't already exist.
    """

    def __init__(self, db_path: Path) -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        # WAL matters more here than it does for a single-process store: the
        # autonomy event stream is written by an operator CLI and read by the
        # governed effect's runtime, often in different processes at the same
        # time. Under the default rollback journal a writer blocks readers
        # outright, so an in-flight demotion would stall (or fail) every
        # concurrent position read. synchronous=NORMAL is WAL's usual
        # companion. Both mirror the settings of the Db this store's queries
        # were lifted from, so concurrency behavior is unchanged from before
        # the extraction.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA_DDL)
        self._conn.commit()

    def close(self) -> None:
        """Close the underlying connection."""
        self._conn.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Deferred (``BEGIN``) transaction: commits on clean exit, rolls
        back on any exception. Not reentrant — see module docstring."""
        self._conn.execute("BEGIN")
        try:
            yield
        except BaseException:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()

    @contextmanager
    def begin_immediate(self) -> Iterator[None]:
        """Immediate-locking (``BEGIN IMMEDIATE``) transaction: same
        commit/rollback contract as ``transaction()``. Not reentrant —
        see module docstring."""
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()

    def insert_autonomy_event(
        self,
        *,
        event_id: str,
        motion_id: str,
        task: str,
        declaration_version: int,
        scope: str | None,
        event: AutonomyEventType,
        from_position: AutonomyPosition,
        to_position: AutonomyPosition,
        evidence_ref: str,
        evidence_sha256: str,
        actor: str,
        reason: str,
        created_at: str,
        event_schema: str = CURRENT_EVENT_SCHEMA,
    ) -> None:
        """Append one autonomy_events row.

        Callers must already be inside a ``transaction()`` or
        ``begin_immediate()`` context — this issues a single INSERT and
        relies on the caller's transaction boundary for atomicity with
        any sibling event write (e.g. approve's motion_approved +
        position_changed pair).
        """
        self._conn.execute(
            """
            INSERT INTO autonomy_events (
                id, event_schema, motion_id, task, declaration_version, scope, event,
                from_position, to_position, evidence_ref, evidence_sha256,
                actor, reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id, event_schema, motion_id, task, declaration_version, scope, event,
                from_position, to_position, evidence_ref, evidence_sha256,
                actor, reason, created_at,
            ),
        )

    def get_autonomy_events_for_motion(self, motion_id: str) -> list[AutonomyEvent]:
        """Every event recorded for one motion, oldest first."""
        rows = self._conn.execute(
            "SELECT * FROM autonomy_events WHERE motion_id = ? ORDER BY created_at, id",
            (motion_id,),
        ).fetchall()
        return [_row_to_event(row) for row in rows]

    def get_latest_position_changed_event(
        self,
        *,
        task: str,
        declaration_version: int,
        scope: str | None,
    ) -> AutonomyEvent | None:
        """The most recent position_changed event for the exact (task,
        declaration_version, scope) triple, or None if authority has
        never moved from the declaration's initial_position under that
        exact version and scope.

        Uses ``scope IS ?`` rather than ``scope = ?`` so a null *scope*
        matches null-scope rows — SQLite's ``=`` never matches NULL.
        """
        row = self._conn.execute(
            """
            SELECT * FROM autonomy_events
            WHERE task = ? AND declaration_version = ? AND scope IS ?
              AND event = 'position_changed'
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (task, declaration_version, scope),
        ).fetchone()
        return _row_to_event(row) if row is not None else None

    def get_position_changed_event_before(
        self,
        *,
        task: str,
        declaration_version: int,
        scope: str | None,
        created_at: str,
        event_id: str,
    ) -> AutonomyEvent | None:
        """The position_changed event that was latest strictly before the
        given (created_at, event_id) point, or None if none existed yet.

        Used to detect an intervening position change between a
        proposal and its approval: comparing this baseline against the
        *current* latest position_changed event catches a position that
        cycled back to the same value through a demotion and
        re-promotion, which a plain value comparison would miss. The
        ``(created_at < ? OR (created_at = ? AND id < ?))`` clause is a
        strict tuple comparison, not a timestamp-only one. Also uses
        ``scope IS ?`` for the same null-scope reason as
        ``get_latest_position_changed_event``.
        """
        row = self._conn.execute(
            """
            SELECT * FROM autonomy_events
            WHERE task = ? AND declaration_version = ? AND scope IS ?
              AND event = 'position_changed'
              AND (created_at < ? OR (created_at = ? AND id < ?))
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (task, declaration_version, scope, created_at, created_at, event_id),
        ).fetchone()
        return _row_to_event(row) if row is not None else None

    def get_autonomy_events(self, *, task: str | None = None) -> list[AutonomyEvent]:
        """Every autonomy event, oldest first, optionally filtered to one task."""
        if task is None:
            rows = self._conn.execute(
                "SELECT * FROM autonomy_events ORDER BY created_at, id"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM autonomy_events WHERE task = ? ORDER BY created_at, id",
                (task,),
            ).fetchall()
        return [_row_to_event(row) for row in rows]


def _assert_implements_event_store(store: SqliteEventStore) -> EventStore:
    """Never called — exists only so mypy checks that SqliteEventStore
    structurally conforms to the EventStore protocol at import time."""
    return store
