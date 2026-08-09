"""Typed vocabulary for the append-only PAA autonomy event stream.

This module owns the domain values the event-sourced control plane writes
and reads: the four autonomy_events.event values, the four autonomy
positions (mirroring paa_runtime.declarations.AutonomyPosition so both modules
type-check against the same literal), the four derived motion statuses
that callers project from event history, and the id/timestamp
generators every event write uses. It holds no database or CLI logic —
see paa_runtime.sqlite_store for the append-only table and
paa_runtime.service for the propose/approve/reject/demote workflows built
on top of it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from paa_runtime.declarations import AutonomyPosition

AutonomyEventType = Literal[
    "motion_proposed", "motion_approved", "motion_rejected", "position_changed",
]

MotionStatus = Literal["proposed", "approved", "rejected", "executed"]

AUTONOMY_EVENT_TYPES: frozenset[str] = frozenset({
    "motion_proposed", "motion_approved", "motion_rejected", "position_changed",
})

AUTONOMY_POSITIONS: frozenset[str] = frozenset({"manual", "hitl", "hotl", "autonomous"})

MOTION_STATUSES: frozenset[str] = frozenset({"proposed", "approved", "rejected", "executed"})

# The event_schema value the published paa.dev autonomy-event schema requires
# on every event record; the store stamps it on each row.
CURRENT_EVENT_SCHEMA: str = "paa-autonomy-event/0.1.0-draft"

__all__ = [
    "AUTONOMY_EVENT_TYPES",
    "AUTONOMY_POSITIONS",
    "CURRENT_EVENT_SCHEMA",
    "MOTION_STATUSES",
    "AutonomyEventType",
    "AutonomyPosition",
    "MotionStatus",
    "new_created_at",
    "new_event_id",
    "new_motion_id",
]


def new_motion_id() -> str:
    """A fresh UUID4 string identifying one proposal-through-resolution motion."""
    return str(uuid.uuid4())


def new_event_id() -> str:
    """A fresh UUID4 string identifying one autonomy_events row."""
    return str(uuid.uuid4())


def new_created_at() -> str:
    """The current instant as a UTC ISO-8601 timestamp with microsecond
    precision and a literal 'Z' suffix, e.g. '2026-07-16T12:34:56.789012Z'."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
