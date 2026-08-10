"""The event store and lifecycle against the published motion histories.

Two claims of different strength. Round-trip says the store can carry a
published history without losing or altering a field. Producibility is
the stronger one: driving the ordinary lifecycle API — propose, then
approve or reject — *generates* those same histories, so the fixtures
describe what this implementation actually writes rather than a shape it
merely tolerates.

Both compare events keyed by kind rather than by list position. Each
motion carries at most one event of each kind (``event.duplicate_event_kind``
is itself a published invalid case), and two events written inside one
transaction can land on the same microsecond, which would leave a
positional comparison to be decided by UUID order.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import paa_contracts as contracts
import pytest
from jsonschema import Draft202012Validator, FormatChecker

from paa_runtime import (
    RuntimeConfig,
    SqliteEventStore,
    approve,
    compute_sha256,
    demote,
    propose,
    reject,
    show,
)

EVENT_SCHEMA: dict[str, Any] = contracts.load_schema("paa-autonomy-event")

DECISION_ARTIFACTS_ROOT = contracts.RUNTIME_FIXTURES_ROOT / "decision-artifacts"

#: Fields the runtime generates per run. Everything else must reproduce.
GENERATED_FIELDS = frozenset({"id", "motion_id", "created_at"})

#: A history whose from_position is not the task's initial position needs
#: the store moved there first. The demotion fixture starts at hotl, which
#: refund_approval only reaches by being promoted.
PRECONDITION = {
    "refund_approval_demotion_immediate.json": "refund_approval_promotion_approved.json",
}

SEQUENCE_PATHS = contracts.autonomy_event_paths()


def _load(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
    return events


def _evidence_path(event: dict[str, Any]) -> Path:
    return DECISION_ARTIFACTS_ROOT / event["evidence_ref"]


def _by_kind(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_kind = {event["event"]: event for event in events}
    assert len(by_kind) == len(events), "a motion carries at most one event per kind"
    return by_kind


def _comparable(event: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in event.items() if k not in GENERATED_FIELDS}


def _replay(
    store: SqliteEventStore, config: RuntimeConfig, events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Drive the lifecycle API to reproduce *events*, and return what it wrote."""
    by_kind = _by_kind(events)
    proposed = by_kind["motion_proposed"]

    motion = propose(
        store, config,
        task=proposed["task"],
        scope=proposed["scope"],
        to_position=proposed["to_position"],
        evidence_path=_evidence_path(proposed),
        actor=proposed["actor"],
        reason=proposed["reason"],
    )

    if (approved := by_kind.get("motion_approved")) is not None:
        approve(
            store, config,
            motion_id=motion.motion_id,
            reason=approved["reason"],
            actor=approved["actor"],
        )
    elif (rejected := by_kind.get("motion_rejected")) is not None:
        reject(
            store, config,
            motion_id=motion.motion_id,
            reason=rejected["reason"],
            actor=rejected["actor"],
        )

    return [
        event.to_json_dict()
        for event in store.get_autonomy_events_for_motion(motion.motion_id)
    ]


@pytest.fixture
def store(runtime_config: RuntimeConfig) -> Any:
    opened = SqliteEventStore(runtime_config.db_path)
    try:
        yield opened
    finally:
        opened.close()


class TestPublishedHistoriesAreValid:
    def test_every_sequence_is_discoverable(self) -> None:
        assert len(SEQUENCE_PATHS) == 5

    @pytest.mark.parametrize("path", SEQUENCE_PATHS, ids=lambda p: p.name)
    def test_every_event_validates_against_the_published_schema(self, path: Path) -> None:
        validator = Draft202012Validator(EVENT_SCHEMA, format_checker=FormatChecker())
        for event in _load(path):
            validator.validate(event)


class TestRoundTrip:
    """insert → read → field-identical."""

    @pytest.mark.parametrize("path", SEQUENCE_PATHS, ids=lambda p: p.name)
    def test_sequence_round_trips_through_the_store(
        self, path: Path, store: SqliteEventStore,
    ) -> None:
        events = _load(path)

        with store.transaction():
            for event in events:
                store.insert_autonomy_event(
                    event_id=event["id"],
                    motion_id=event["motion_id"],
                    task=event["task"],
                    declaration_version=event["declaration_version"],
                    scope=event["scope"],
                    event=event["event"],
                    from_position=event["from_position"],
                    to_position=event["to_position"],
                    evidence_ref=event["evidence_ref"],
                    evidence_sha256=event["evidence_sha256"],
                    actor=event["actor"],
                    reason=event["reason"],
                    created_at=event["created_at"],
                    event_schema=event["event_schema"],
                )

        stored = store.get_autonomy_events_for_motion(events[0]["motion_id"])

        # Reads are ordered (created_at, id), which is the store's
        # documented guarantee and not necessarily the order the fixture
        # was authored in — the demotion history writes all three of its
        # events on one timestamp.
        assert [event.to_json_dict() for event in stored] == sorted(
            events, key=lambda e: (e["created_at"], e["id"])
        )


class TestProducibility:
    """Replaying the lifecycle generates the published history."""

    @pytest.mark.parametrize("path", SEQUENCE_PATHS, ids=lambda p: p.name)
    def test_sequence_is_producible(
        self, path: Path, store: SqliteEventStore, runtime_config: RuntimeConfig,
    ) -> None:
        if (precondition := PRECONDITION.get(path.name)) is not None:
            _replay(store, runtime_config, _load(path.parent / precondition))

        expected = _by_kind(_load(path))
        produced = _by_kind(_replay(store, runtime_config, _load(path)))

        assert set(produced) == set(expected)
        for kind, expected_event in expected.items():
            assert _comparable(produced[kind]) == _comparable(expected_event), kind

    @pytest.mark.parametrize("path", SEQUENCE_PATHS, ids=lambda p: p.name)
    def test_replay_re_derives_the_published_content_address(
        self, path: Path, store: SqliteEventStore, runtime_config: RuntimeConfig,
    ) -> None:
        # The replay is handed evidence *bytes* and must arrive at the
        # same evidence_ref the published history records. This is the
        # assertion that would catch a change to how the runtime hashes
        # or lays out content-addressed evidence.
        if (precondition := PRECONDITION.get(path.name)) is not None:
            _replay(store, runtime_config, _load(path.parent / precondition))

        expected = _load(path)[0]
        produced = _replay(store, runtime_config, _load(path))

        assert {event["evidence_sha256"] for event in produced} == {
            expected["evidence_sha256"]
        }
        assert {event["evidence_ref"] for event in produced} == {expected["evidence_ref"]}

    @pytest.mark.parametrize("path", SEQUENCE_PATHS, ids=lambda p: p.name)
    def test_position_folds_to_the_history_s_final_position(
        self, path: Path, store: SqliteEventStore, runtime_config: RuntimeConfig,
    ) -> None:
        if (precondition := PRECONDITION.get(path.name)) is not None:
            _replay(store, runtime_config, _load(path.parent / precondition))

        events = _load(path)
        _replay(store, runtime_config, events)

        by_kind = _by_kind(events)
        changed = by_kind.get("position_changed")
        proposed = by_kind["motion_proposed"]
        # A rejected motion must leave the position exactly where it was.
        expected_position = (
            changed["to_position"] if changed is not None else proposed["from_position"]
        )

        resolved = show(
            store, runtime_config, task=proposed["task"], scope=proposed["scope"],
        )
        assert resolved["current_position"] == expected_position


class TestOneCommandDemotion:
    """``demote`` writes the demotion history, with its own evidence.

    The published demotion history binds to a ``paa-decision-artifact``
    document. ``demote`` does not accept one: it generates and
    content-addresses its own evidence so an emergency demotion never
    blocks on an operator producing an artifact first. So its event
    stream reproduces the published one in every field except the two
    that name the evidence.

    That divergence is real and is asserted here rather than skipped —
    a suite that omitted this fixture would leave the one-command path
    unexercised, and a suite that compared evidence fields would fail for
    a reason nobody intends to fix.
    """

    FIXTURE = "refund_approval_demotion_immediate.json"

    def _demoted(
        self, store: SqliteEventStore, runtime_config: RuntimeConfig,
    ) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        path = contracts.RUNTIME_FIXTURES_ROOT / "autonomy-events" / self.FIXTURE
        expected = _by_kind(_load(path))
        proposed = expected["motion_proposed"]

        _replay(
            store, runtime_config,
            _load(path.parent / PRECONDITION[self.FIXTURE]),
        )
        motion = demote(
            store, runtime_config,
            task=proposed["task"],
            scope=proposed["scope"],
            reason=proposed["reason"],
            actor=proposed["actor"],
        )
        produced = _by_kind([
            event.to_json_dict()
            for event in store.get_autonomy_events_for_motion(motion.motion_id)
        ])
        return expected, produced

    def test_demote_writes_the_published_event_stream(
        self, store: SqliteEventStore, runtime_config: RuntimeConfig,
    ) -> None:
        expected, produced = self._demoted(store, runtime_config)
        evidence_fields = {"evidence_ref", "evidence_sha256"}

        assert set(produced) == set(expected)
        for kind, expected_event in expected.items():
            assert {
                k: v for k, v in _comparable(produced[kind]).items()
                if k not in evidence_fields
            } == {
                k: v for k, v in _comparable(expected_event).items()
                if k not in evidence_fields
            }, kind

    def test_demote_binds_to_evidence_it_generated_itself(
        self, store: SqliteEventStore, runtime_config: RuntimeConfig,
    ) -> None:
        expected, produced = self._demoted(store, runtime_config)
        published_sha = expected["motion_proposed"]["evidence_sha256"]

        generated = {event["evidence_sha256"] for event in produced.values()}
        assert len(generated) == 1, "all three events bind to one artifact"
        assert generated != {published_sha}

        # Weaker than reproducing the published address, but still a real
        # claim: the artifact exists, at the address the events name, with
        # bytes that hash to the digest they record.
        sha = generated.pop()
        artifact = runtime_config.evidence_root / f"evidence/paa/{sha}/evidence.json"
        assert artifact.is_file()
        assert compute_sha256(artifact.read_bytes()) == sha
