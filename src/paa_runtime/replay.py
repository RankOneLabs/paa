"""Import a contract-shaped autonomy-event archive into an event store."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, cast

from paa_runtime.events import AutonomyEventType, AutonomyPosition
from paa_runtime.store import EventStore

__all__ = ["import_events"]


def import_events(store: EventStore, events: Iterable[Mapping[str, Any]]) -> None:
    """Append an exported event sequence without changing any contract field.

    Structural validation belongs to ``paa-contracts``. This function is the
    deliberately small runtime boundary used after a contract-shaped archive
    has passed that validation: one transaction, original identifiers and
    timestamps preserved, and no lifecycle events synthesized.
    """
    with store.transaction():
        for event in events:
            store.insert_autonomy_event(
                event_id=cast(str, event["id"]),
                motion_id=cast(str, event["motion_id"]),
                task=cast(str, event["task"]),
                declaration_version=cast(int, event["declaration_version"]),
                scope=cast(str | None, event["scope"]),
                event=cast(AutonomyEventType, event["event"]),
                from_position=cast(AutonomyPosition, event["from_position"]),
                to_position=cast(AutonomyPosition, event["to_position"]),
                evidence_ref=cast(str, event["evidence_ref"]),
                evidence_sha256=cast(str, event["evidence_sha256"]),
                actor=cast(str, event["actor"]),
                reason=cast(str, event["reason"]),
                created_at=cast(str, event["created_at"]),
                event_schema=cast(str, event["event_schema"]),
            )
