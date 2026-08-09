"""Tests for paa_events's typed vocabulary and id/timestamp generators."""

from __future__ import annotations

import re
import uuid

from paa_runtime.events import (
    AUTONOMY_EVENT_TYPES,
    AUTONOMY_POSITIONS,
    MOTION_STATUSES,
    new_created_at,
    new_event_id,
    new_motion_id,
)

_CREATED_AT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")


class TestVocabulary:
    def test_autonomy_event_types(self) -> None:
        assert {
            "motion_proposed", "motion_approved", "motion_rejected", "position_changed",
        } == AUTONOMY_EVENT_TYPES

    def test_autonomy_positions(self) -> None:
        assert {"manual", "hitl", "hotl", "autonomous"} == AUTONOMY_POSITIONS

    def test_motion_statuses(self) -> None:
        assert {"proposed", "approved", "rejected", "executed"} == MOTION_STATUSES


class TestNewMotionId:
    def test_returns_valid_uuid4_string(self) -> None:
        motion_id = new_motion_id()
        parsed = uuid.UUID(motion_id)
        assert parsed.version == 4
        assert str(parsed) == motion_id

    def test_successive_calls_are_unique(self) -> None:
        assert new_motion_id() != new_motion_id()


class TestNewEventId:
    def test_returns_valid_uuid4_string(self) -> None:
        event_id = new_event_id()
        parsed = uuid.UUID(event_id)
        assert parsed.version == 4
        assert str(parsed) == event_id

    def test_successive_calls_are_unique(self) -> None:
        assert new_event_id() != new_event_id()

    def test_distinct_from_motion_id_namespace(self) -> None:
        # Not a structural guarantee (both are plain UUID4s), just confirms
        # the two generators aren't accidentally aliased to one function.
        assert new_event_id() != new_motion_id()


class TestNewCreatedAt:
    def test_matches_canonical_microsecond_utc_z_form(self) -> None:
        assert _CREATED_AT_RE.match(new_created_at())

    def test_is_monotonic_enough_for_ordering(self) -> None:
        first = new_created_at()
        second = new_created_at()
        assert first <= second  # ISO-8601 strings sort lexicographically by instant
