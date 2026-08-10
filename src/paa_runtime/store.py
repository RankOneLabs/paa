"""The append-only autonomy event store: protocol and row type.

``EventStore`` is the storage seam ``paa_runtime.service`` (the workflow
layer: propose / approve / reject / demote) is written against — one
insert, four reads, and two transaction context managers, and nothing
else.

Most consumers never implement it: ``paa_runtime.sqlite_store.
SqliteEventStore`` is the default, the runtime owns its own database, and
no consumer has to host a table to adopt the package. The protocol stays
deliberately small for the consumer that does need to override it — one
whose governed effect and the position read authorizing it must commit in
a single lock domain, which a runtime-owned store cannot offer. That
claim-atomicity trade-off is written up under "Overriding the store" in
the README; it is a named limitation and an intentional escape hatch,
not an oversight.

This module holds no SQL and no schema — see
``paa_runtime.sqlite_store`` for the default implementation's DDL,
triggers, and query bodies.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Protocol

from paa_runtime.events import CURRENT_EVENT_SCHEMA, AutonomyEventType, AutonomyPosition

__all__ = ["AutonomyEvent", "EventStore"]


@dataclass(frozen=True, slots=True)
class AutonomyEvent:
    """One row of the append-only autonomy event stream.

    Field order matches the ``required`` array of the published
    ``paa-autonomy-event.schema.json`` exactly (fourteen fields), which is
    what makes the conformance suite's round-trip assertion checkable.
    ``id`` — not ``event_id`` — is the row's own identity: it is the
    published schema's field name and the name every read site in the
    service layer uses (``event_id`` is only the *insert* keyword, kept
    that way for continuity with existing call sites). This dataclass is
    the contract representation returned by every ``EventStore`` reader —
    implementations must never leak their underlying storage row (e.g.
    ``sqlite3.Row``) past this boundary.
    """

    event_schema: str
    id: str
    motion_id: str
    task: str
    declaration_version: int
    scope: str | None
    event: AutonomyEventType
    from_position: AutonomyPosition
    to_position: AutonomyPosition
    evidence_ref: str
    evidence_sha256: str
    actor: str
    reason: str
    created_at: str

    def to_json_dict(self) -> dict[str, Any]:
        """All fourteen fields, in contract order.

        This is the operator-facing representation: consumed both by the
        conformance suite's round-trip check and by the service layer's
        ``ResolvedPosition.latest_position_event`` / ``show()`` JSON
        output, so key names and ordering here are load-bearing.
        """
        return {
            "event_schema": self.event_schema,
            "id": self.id,
            "motion_id": self.motion_id,
            "task": self.task,
            "declaration_version": self.declaration_version,
            "scope": self.scope,
            "event": self.event,
            "from_position": self.from_position,
            "to_position": self.to_position,
            "evidence_ref": self.evidence_ref,
            "evidence_sha256": self.evidence_sha256,
            "actor": self.actor,
            "reason": self.reason,
            "created_at": self.created_at,
        }


class EventStore(Protocol):
    """Append-only autonomy event storage.

    Implementations MUST reject mutation of existing events at the
    storage layer, not by convention (see the SQLite triggers in
    ``paa_runtime.sqlite_store.SqliteEventStore`` for the default's
    backstop). Events are returned oldest-first unless documented
    otherwise. Transaction contexts do NOT nest.

    Five semantics are load-bearing for every implementation, not just
    the default one:

    1. **Transaction boundary.** ``insert_autonomy_event`` requires the
       caller to already hold a transaction — it issues a single insert
       and relies on the caller's ``transaction()`` / ``begin_immediate()``
       boundary for atomicity with any sibling event write. Approve, for
       example, writes a ``motion_approved`` + ``position_changed`` pair
       atomically under one boundary; the store never opens its own.
       Implementations SHOULD reject an insert issued outside a
       transaction rather than quietly accepting it: a backend that
       auto-commits the orphan write splits that pair silently, and the
       resulting history — an approval with no position change — is
       indistinguishable from corruption after the fact.
    2. **Ordering.** Reads are ordered ``(created_at, id)`` ascending;
       latest-position lookups are ordered ``(created_at, id)``
       descending. ``get_position_changed_event_before`` compares
       ``(created_at, id)`` as a strict tuple — not `created_at` alone —
       against its baseline point. That tuple comparison is what detects
       a position that cycled away and back to the same value between a
       motion's proposal and its approval; a plain value comparison on
       the resolved position would miss it. Any alternative store must
       reproduce this tuple-ordering semantic exactly, not approximate it
       with a timestamp-only comparison.
    3. **Uniqueness.** At most one event of each ``event`` type per
       ``motion_id`` — enforced as a ``(motion_id, event)`` unique
       constraint, not merely by caller discipline.
    4. **Append-only.** Enforced by the store itself (e.g. triggers that
       abort UPDATE/DELETE), not by convention or by callers being
       well-behaved.
    5. **No nesting.** Transaction contexts do not nest — no runtime code
       path in ``paa_runtime.service`` opens one transaction context
       inside another (a plain read such as ``resolve_current_position``
       is called before or inside a transaction, never opening its own).
       Implementations MAY be reentrant if that's convenient for them,
       but must not require reentrancy to be used correctly.
    """

    def transaction(self) -> AbstractContextManager[None]:
        """Deferred transaction context: commits on clean exit, rolls
        back on any exception. Callers hold it around one or more
        ``insert_autonomy_event`` calls that must land atomically."""
        ...

    def begin_immediate(self) -> AbstractContextManager[None]:
        """Immediate-locking transaction context: same commit/rollback
        contract as ``transaction()``, but acquires the write lock
        up front rather than deferring it to the first write."""
        ...

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
        """Append one autonomy_events row. See semantic 1: the caller
        must already hold a transaction() or begin_immediate() context."""
        ...

    def get_autonomy_events_for_motion(self, motion_id: str) -> list[AutonomyEvent]:
        """Every event recorded for one motion, oldest first."""
        ...

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
        exact version and scope. ``scope=None`` matches rows whose scope
        is null, not rows where scope is unset or 'any'."""
        ...

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
        re-promotion, which a plain value comparison would miss (see
        semantic 2)."""
        ...

    def get_autonomy_events(self, *, task: str | None = None) -> list[AutonomyEvent]:
        """Every autonomy event, oldest first, optionally filtered to one
        task."""
        ...
