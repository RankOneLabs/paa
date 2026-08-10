"""Tests for paa_runtime.service's propose/approve/reject/demote/resolve/
show/list workflows — the core of the event-sourced PAA autonomy control
plane.

Ported from Scout's tests/test_paa_service.py. Scout's in_memory_state
fixture, its module-global PRODUCER_REGISTRY, and its checked-in
contracts/paa declarations directory don't exist here — this package
ships no conftest, so a real SqliteEventStore against a tmp_path database
plus small local declaration fixtures (mirroring tests/test_declarations.
py's approach) stand in for them.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

import paa_runtime.service as svc
from paa_runtime.config import RuntimeConfig
from paa_runtime.declarations import (
    PaaDeclarationError,
    PaaEvaluationBasis,
    ProducerRegistration,
)
from paa_runtime.events import CURRENT_EVENT_SCHEMA
from paa_runtime.evidence import EvidenceError
from paa_runtime.sqlite_store import SqliteEventStore

OUTBOUND = "outbound_content_publish"
INBOUND = "inbound_reply_surfacing"
BLUESKY = "publish:bluesky"
FARCASTER = "publish:farcaster"

_REGISTRY: tuple[ProducerRegistration, ...] = (
    ProducerRegistration(
        property="outbound_content_invariants", target="output", technique="deterministic",
        evaluation_basis=PaaEvaluationBasis(
            kind="invariant", ref="outbound_content_invariants",
        ),
        epistemic_status="ground_truth",
        version="1", authority="blocking", status="implemented",
    ),
    ProducerRegistration(
        property="content_invariants", target="output", technique="deterministic",
        evaluation_basis=PaaEvaluationBasis(kind="invariant", ref="content_invariants"),
        epistemic_status="ground_truth",
        version="1", authority="blocking", status="implemented",
    ),
)

_FIXED_POSITION_POLICY: dict[str, object] = {
    "manual": "offline",
    "hitl": "blocking",
    "hotl": "async",
    "autonomous": "offline",
}

# Mirrors Scout's outbound_content_publish.v1.yaml: active deployment, a
# declared scopes block, hitl<->hotl promotion/demotion — the declaration
# B1's "declares scopes" branch and the scope-allowlist tests exercise.
_OUTBOUND_TASK: dict[str, object] = {
    "task": OUTBOUND,
    "version": 1,
    "deployment": "active",
    "initial_position": "hitl",
    "scopes": [BLUESKY, FARCASTER],
    "evaluators": [
        {
            "property": "outbound_content_invariants", "target": "output",
            "technique": "deterministic",
            "evaluation_basis": {"kind": "invariant", "ref": "outbound_content_invariants"},
            "epistemic_status": "ground_truth",
            "version": "1", "authority": "blocking",
        },
    ],
    "position_policy": dict(_FIXED_POSITION_POLICY),
    "promotion": {
        "from": "hitl", "to": "hotl", "report": "outbound_publish_promotion_report",
        "window": {"kind": "cases", "size": 50}, "execution": "operator_approval",
    },
    "demotion": {
        "from": "hotl", "to": "hitl", "trigger": "operator_decision_or_policy_failure",
        "window": {"kind": "cases", "size": 1},
    },
}

# Mirrors Scout's inbound_reply_surfacing.v1.yaml: no scopes block — the
# declaration B1's "declares no scopes" branch exercises.
_INBOUND_TASK: dict[str, object] = {
    "task": INBOUND,
    "version": 1,
    "deployment": "shadow",
    "initial_position": "hitl",
    "evaluators": [
        {
            "property": "content_invariants", "target": "output",
            "technique": "deterministic",
            "evaluation_basis": {"kind": "invariant", "ref": "content_invariants"},
            "epistemic_status": "ground_truth",
            "version": "1", "authority": "blocking",
        },
    ],
    "position_policy": dict(_FIXED_POSITION_POLICY),
    "promotion": {
        "from": "hitl", "to": "hotl", "report": "phase1_audit",
        "window": {"kind": "duration", "size": "P14D"}, "execution": "operator_approval",
    },
    "demotion": {
        "from": "hotl", "to": "hitl", "trigger": "operator_decision_or_policy_failure",
        "window": {"kind": "cases", "size": 1},
    },
}


def _write_declaration(directory: Path, document: dict[str, object]) -> Path:
    path = directory / f"{document['task']}.v{document['version']}.yaml"
    path.write_text(yaml.safe_dump(document))
    return path


def _versioned_config(
    config: RuntimeConfig, tmp_path: Path, *, version: int, initial_position: str = "manual",
) -> RuntimeConfig:
    """A RuntimeConfig pointed at a one-task declarations directory cloned
    from _OUTBOUND_TASK with version/initial_position overridden — mirrors
    Scout's _write_versioned_declaration technique for isolating a
    declaration-version bump in a test."""
    document = dict(_OUTBOUND_TASK)
    document["version"] = version
    document["initial_position"] = initial_position
    out_dir = tmp_path / f"decls_v{version}"
    out_dir.mkdir()
    _write_declaration(out_dir, document)
    return replace(config, declarations_dir=out_dir)


@pytest.fixture
def declarations_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "declarations"
    directory.mkdir()
    _write_declaration(directory, _OUTBOUND_TASK)
    _write_declaration(directory, _INBOUND_TASK)
    return directory


@pytest.fixture
def evidence_root(tmp_path: Path) -> Path:
    return tmp_path / "evroot"


@pytest.fixture
def config(declarations_dir: Path, evidence_root: Path, tmp_path: Path) -> RuntimeConfig:
    return RuntimeConfig(
        declarations_dir=declarations_dir,
        evidence_root=evidence_root,
        registry=_REGISTRY,
        db_path=tmp_path / "autonomy_events.db",
        actor_env_var="SCOUT_PAA_ACTOR",
    )


@pytest.fixture
def store(config: RuntimeConfig) -> Iterator[SqliteEventStore]:
    event_store = SqliteEventStore(config.db_path)
    yield event_store
    event_store.close()


@pytest.fixture
def evidence_file(tmp_path: Path) -> Path:
    path = tmp_path / "evidence-a.json"
    path.write_text('{"report": "a"}')
    return path


@pytest.fixture
def evidence_file_b(tmp_path: Path) -> Path:
    path = tmp_path / "evidence-b.json"
    path.write_text('{"report": "b"}')
    return path


class TestResolveCurrentPosition:
    def test_initial_position_with_no_events(
        self, store: SqliteEventStore, config: RuntimeConfig,
    ) -> None:
        resolved = svc.resolve_current_position(store, config, OUTBOUND, BLUESKY)
        assert resolved.current_position == "hitl"
        assert resolved.latest_position_event is None
        assert resolved.deployment == "active"
        assert resolved.declaration_version == 1

    def test_latest_event_wins_by_created_at_then_id(
        self, store: SqliteEventStore, config: RuntimeConfig,
    ) -> None:
        with store.transaction():
            for event_id, created_at, to_pos in (
                ("e1", "2026-01-01T00:00:00.000000Z", "hotl"),
                ("e2", "2026-01-02T00:00:00.000000Z", "hitl"),
                ("e3", "2026-01-02T00:00:00.000000Z", "hotl"),  # tie on created_at, higher id wins
            ):
                store.insert_autonomy_event(
                    event_id=event_id, motion_id=f"m-{event_id}", task=OUTBOUND,
                    declaration_version=1, scope=BLUESKY, event="position_changed",
                    from_position="hitl", to_position=to_pos,  # type: ignore[arg-type]
                    evidence_ref="evidence/paa/" + "a" * 64 + "/evidence.json",
                    evidence_sha256="a" * 64, actor="steve", reason="r", created_at=created_at,
                )
        resolved = svc.resolve_current_position(store, config, OUTBOUND, BLUESKY)
        assert resolved.current_position == "hotl"
        assert resolved.latest_position_event is not None
        assert resolved.latest_position_event["id"] == "e3"

    def test_latest_position_event_carries_event_schema(
        self, store: SqliteEventStore, config: RuntimeConfig,
    ) -> None:
        """B4: AutonomyEvent.to_json_dict() is contract-shaped, so
        latest_position_event gains an event_schema key — additive only,
        no key renamed, removed, or reordered (spec section 1, B4)."""
        with store.transaction():
            store.insert_autonomy_event(
                event_id="e1", motion_id="m1", task=OUTBOUND, declaration_version=1,
                scope=BLUESKY, event="position_changed", from_position="hitl",
                to_position="hotl", evidence_ref="evidence/paa/" + "a" * 64 + "/evidence.json",
                evidence_sha256="a" * 64, actor="steve", reason="r",
                created_at="2026-01-01T00:00:00.000000Z",
            )
        resolved = svc.resolve_current_position(store, config, OUTBOUND, BLUESKY)
        assert resolved.latest_position_event is not None
        assert resolved.latest_position_event["event_schema"] == CURRENT_EVENT_SCHEMA

    def test_exact_scope_isolation(
        self, store: SqliteEventStore, config: RuntimeConfig,
    ) -> None:
        with store.transaction():
            store.insert_autonomy_event(
                event_id="e1", motion_id="m1", task=OUTBOUND, declaration_version=1,
                scope=BLUESKY, event="position_changed", from_position="hitl",
                to_position="hotl", evidence_ref="evidence/paa/" + "a" * 64 + "/evidence.json",
                evidence_sha256="a" * 64, actor="steve", reason="r",
                created_at="2026-01-01T00:00:00.000000Z",
            )

        bluesky = svc.resolve_current_position(store, config, OUTBOUND, BLUESKY)
        farcaster = svc.resolve_current_position(store, config, OUTBOUND, FARCASTER)
        assert bluesky.current_position == "hotl"
        assert farcaster.current_position == "hitl"  # untouched — no wildcard leak

    def test_declaration_version_bump_resets_authority(
        self, store: SqliteEventStore, config: RuntimeConfig, tmp_path: Path,
    ) -> None:
        # Position moves to hotl under declaration version 1.
        with store.transaction():
            store.insert_autonomy_event(
                event_id="e1", motion_id="m1", task=OUTBOUND, declaration_version=1,
                scope=BLUESKY, event="position_changed", from_position="hitl",
                to_position="hotl", evidence_ref="evidence/paa/" + "a" * 64 + "/evidence.json",
                evidence_sha256="a" * 64, actor="steve", reason="r",
                created_at="2026-01-01T00:00:00.000000Z",
            )

        config_v2 = _versioned_config(config, tmp_path, version=2, initial_position="manual")
        resolved_v2 = svc.resolve_current_position(store, config_v2, OUTBOUND, BLUESKY)
        # Version 2 never reads version 1's position_changed events — it
        # falls back to its own declared initial_position.
        assert resolved_v2.current_position == "manual"
        assert resolved_v2.latest_position_event is None
        assert resolved_v2.declaration_version == 2

        # Version 1 resolution is untouched by the existence of v2.
        resolved_v1 = svc.resolve_current_position(store, config, OUTBOUND, BLUESKY)
        assert resolved_v1.current_position == "hotl"

    def test_unknown_task_raises_declaration_error(
        self, store: SqliteEventStore, config: RuntimeConfig,
    ) -> None:
        with pytest.raises(PaaDeclarationError):
            svc.resolve_current_position(store, config, "not_a_real_task", BLUESKY)

    def test_empty_scope_rejected(
        self, store: SqliteEventStore, config: RuntimeConfig,
    ) -> None:
        with pytest.raises(svc.PaaValidationError):
            svc.resolve_current_position(store, config, OUTBOUND, "")


class TestResolvePosition:
    def test_declaration_initial_source_with_no_events(
        self, store: SqliteEventStore, config: RuntimeConfig,
    ) -> None:
        resolution = svc.resolve_position(store, config, OUTBOUND, BLUESKY)
        assert resolution.task == OUTBOUND
        assert resolution.declaration_version == 1
        assert resolution.deployment == "active"
        assert resolution.scope == BLUESKY
        assert resolution.position == "hitl"
        assert resolution.source == "declaration_initial"
        assert resolution.source_event_id is None

    def test_position_event_source_carries_the_exact_event_id(
        self, store: SqliteEventStore, config: RuntimeConfig,
    ) -> None:
        with store.transaction():
            store.insert_autonomy_event(
                event_id="e1", motion_id="m1", task=OUTBOUND, declaration_version=1,
                scope=BLUESKY, event="position_changed", from_position="hitl",
                to_position="hotl", evidence_ref="evidence/paa/" + "a" * 64 + "/evidence.json",
                evidence_sha256="a" * 64, actor="steve", reason="r",
                created_at="2026-01-01T00:00:00.000000Z",
            )

        resolution = svc.resolve_position(store, config, OUTBOUND, BLUESKY)
        assert resolution.position == "hotl"
        assert resolution.source == "position_event"
        assert resolution.source_event_id == "e1"

    def test_exact_scope_isolation(
        self, store: SqliteEventStore, config: RuntimeConfig,
    ) -> None:
        with store.transaction():
            store.insert_autonomy_event(
                event_id="e1", motion_id="m1", task=OUTBOUND, declaration_version=1,
                scope=BLUESKY, event="position_changed", from_position="hitl",
                to_position="hotl", evidence_ref="evidence/paa/" + "a" * 64 + "/evidence.json",
                evidence_sha256="a" * 64, actor="steve", reason="r",
                created_at="2026-01-01T00:00:00.000000Z",
            )

        bluesky = svc.resolve_position(store, config, OUTBOUND, BLUESKY)
        farcaster = svc.resolve_position(store, config, OUTBOUND, FARCASTER)
        assert bluesky.position == "hotl"
        assert bluesky.source == "position_event"
        assert farcaster.position == "hitl"
        assert farcaster.source == "declaration_initial"

    def test_to_position_resolution_matches_resolve_position(
        self, store: SqliteEventStore, config: RuntimeConfig,
    ) -> None:
        resolved = svc.resolve_current_position(store, config, OUTBOUND, BLUESKY)
        assert svc.to_position_resolution(resolved) == svc.resolve_position(
            store, config, OUTBOUND, BLUESKY
        )


class TestPropose:
    def test_propose_has_zero_effect_on_current_position(
        self, store: SqliteEventStore, config: RuntimeConfig, evidence_file: Path,
    ) -> None:
        before = svc.resolve_current_position(store, config, OUTBOUND, BLUESKY)
        motion = svc.propose(
            store, config, task=OUTBOUND, scope=BLUESKY, to_position="hotl",
            evidence_path=evidence_file, actor="steve",
        )
        after = svc.resolve_current_position(store, config, OUTBOUND, BLUESKY)

        assert motion.status == "proposed"
        assert before.current_position == after.current_position == "hitl"
        events = store.get_autonomy_events_for_motion(motion.motion_id)
        assert [e.event for e in events] == ["motion_proposed"]

    def test_propose_generates_default_reason(
        self, store: SqliteEventStore, config: RuntimeConfig, evidence_file: Path,
    ) -> None:
        motion = svc.propose(
            store, config, task=OUTBOUND, scope=BLUESKY, to_position="hotl",
            evidence_path=evidence_file, actor="steve",
        )
        assert motion.proposed_reason == "requested transition hitl to hotl"

    def test_propose_evidence_is_content_addressed(
        self, store: SqliteEventStore, config: RuntimeConfig, evidence_file: Path,
        evidence_root: Path,
    ) -> None:
        motion = svc.propose(
            store, config, task=OUTBOUND, scope=BLUESKY, to_position="hotl",
            evidence_path=evidence_file, actor="steve",
        )
        stored = evidence_root / "evidence" / "paa" / motion.evidence_sha256 / "evidence.json"
        assert stored.read_bytes() == evidence_file.read_bytes()

    def test_invalid_to_position_rejected(
        self, store: SqliteEventStore, config: RuntimeConfig, evidence_file: Path,
    ) -> None:
        with pytest.raises(svc.PaaValidationError):
            svc.propose(
                store, config, task=OUTBOUND, scope=BLUESKY, to_position="not_a_position",
                evidence_path=evidence_file, actor="steve",
            )

    def test_undeclared_transition_rejected(
        self, store: SqliteEventStore, config: RuntimeConfig, evidence_file: Path,
    ) -> None:
        # From hitl, only hitl->hotl (promotion) is declared; manual and
        # autonomous are unreachable.
        with pytest.raises(svc.PaaTransitionError):
            svc.propose(
                store, config, task=OUTBOUND, scope=BLUESKY, to_position="autonomous",
                evidence_path=evidence_file, actor="steve",
            )

    def test_outbound_scope_allowlist_rejects_wildcard(
        self, store: SqliteEventStore, config: RuntimeConfig, evidence_file: Path,
    ) -> None:
        with pytest.raises(svc.PaaValidationError):
            svc.propose(
                store, config, task=OUTBOUND, scope="publish:*", to_position="hotl",
                evidence_path=evidence_file, actor="steve",
            )

    def test_outbound_scope_allowlist_rejects_unknown_platform(
        self, store: SqliteEventStore, config: RuntimeConfig, evidence_file: Path,
    ) -> None:
        with pytest.raises(svc.PaaValidationError):
            svc.propose(
                store, config, task=OUTBOUND, scope="publish:mastodon", to_position="hotl",
                evidence_path=evidence_file, actor="steve",
            )

    def test_task_with_no_scopes_rejects_arbitrary_scope(
        self, store: SqliteEventStore, config: RuntimeConfig, evidence_file: Path,
    ) -> None:
        """B1: scope validity now comes from the declaration's `scopes`
        field rather than a hardcoded outbound_content_publish allowlist.
        A task that declares no scopes (INBOUND here) no longer accepts an
        arbitrary scope string — it now requires scope=None. This is the
        one existing test the spec names as expected to flip (spec
        section 1, B1; extraction spec §3.4)."""
        with pytest.raises(svc.PaaValidationError):
            svc.propose(
                store, config, task=INBOUND, scope="discord:general", to_position="hotl",
                evidence_path=evidence_file, actor="steve",
            )

    def test_task_with_no_scopes_accepts_none_scope(
        self, store: SqliteEventStore, config: RuntimeConfig, evidence_file: Path,
    ) -> None:
        """The other half of B1's new rule: a task declaring no scopes
        accepts scope=None."""
        motion = svc.propose(
            store, config, task=INBOUND, scope=None, to_position="hotl",
            evidence_path=evidence_file, actor="steve",
        )
        assert motion.scope is None

    def test_empty_evidence_file_rejected(
        self, store: SqliteEventStore, config: RuntimeConfig, tmp_path: Path,
    ) -> None:
        empty = tmp_path / "empty.json"
        empty.write_text("")
        with pytest.raises(svc.PaaValidationError, match="empty"):
            svc.propose(
                store, config, task=OUTBOUND, scope=BLUESKY, to_position="hotl",
                evidence_path=empty, actor="steve",
            )

    def test_missing_evidence_file_rejected(
        self, store: SqliteEventStore, config: RuntimeConfig, tmp_path: Path,
    ) -> None:
        with pytest.raises(EvidenceError):
            svc.propose(
                store, config, task=OUTBOUND, scope=BLUESKY, to_position="hotl",
                evidence_path=tmp_path / "missing.json", actor="steve",
            )

    def test_actor_resolution_order(
        self,
        store: SqliteEventStore,
        config: RuntimeConfig,
        evidence_file: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SCOUT_PAA_ACTOR", "env-actor")
        explicit = svc.propose(
            store, config, task=OUTBOUND, scope=BLUESKY, to_position="hotl",
            evidence_path=evidence_file, actor="explicit-actor",
        )
        assert explicit.proposed_by == "explicit-actor"

        from_env = svc.propose(
            store, config, task=INBOUND, scope=None, to_position="hotl",
            evidence_path=evidence_file,
        )
        assert from_env.proposed_by == "env-actor"

    def test_actor_env_var_name_is_configurable(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """B2: resolve_actor reads config.actor_env_var, not a hardcoded
        SCOUT_PAA_ACTOR — a differently named variable is honored."""
        monkeypatch.delenv("SCOUT_PAA_ACTOR", raising=False)
        monkeypatch.setenv("SCOUT_PAA_ACTOR", "wrong-actor")
        monkeypatch.setenv("CUSTOM_PAA_ACTOR", "right-actor")
        assert svc.resolve_actor(None, env_var="CUSTOM_PAA_ACTOR") == "right-actor"


class TestApprove:
    def test_fresh_approval_executes_and_moves_position(
        self, store: SqliteEventStore, config: RuntimeConfig, evidence_file: Path,
    ) -> None:
        motion = svc.propose(
            store, config, task=OUTBOUND, scope=BLUESKY, to_position="hotl",
            evidence_path=evidence_file, actor="steve",
        )
        approved = svc.approve(
            store, config, motion_id=motion.motion_id, reason="looks good", actor="ops",
        )
        assert approved.status == "executed"
        assert approved.approved_by == "ops"
        assert approved.executed_at is not None

        resolved = svc.resolve_current_position(store, config, OUTBOUND, BLUESKY)
        assert resolved.current_position == "hotl"

    def test_idempotent_reapproval_returns_existing_without_writes(
        self, store: SqliteEventStore, config: RuntimeConfig, evidence_file: Path,
    ) -> None:
        motion = svc.propose(
            store, config, task=OUTBOUND, scope=BLUESKY, to_position="hotl",
            evidence_path=evidence_file, actor="steve",
        )
        first = svc.approve(store, config, motion_id=motion.motion_id, reason="go", actor="ops")
        events_after_first = store.get_autonomy_events_for_motion(motion.motion_id)

        second = svc.approve(
            store, config, motion_id=motion.motion_id, reason="go again", actor="ops2",
        )
        events_after_second = store.get_autonomy_events_for_motion(motion.motion_id)

        assert first == second
        assert len(events_after_first) == len(events_after_second) == 3

    def test_unknown_motion_id_raises_not_found(
        self, store: SqliteEventStore, config: RuntimeConfig,
    ) -> None:
        with pytest.raises(svc.PaaNotFoundError):
            svc.approve(store, config, motion_id="does-not-exist", reason="go")

    def test_approving_a_rejected_motion_is_terminal(
        self, store: SqliteEventStore, config: RuntimeConfig, evidence_file: Path,
    ) -> None:
        motion = svc.propose(
            store, config, task=OUTBOUND, scope=BLUESKY, to_position="hotl",
            evidence_path=evidence_file, actor="steve",
        )
        svc.reject(store, config, motion_id=motion.motion_id, reason="no")

        with pytest.raises(svc.PaaTerminalError):
            svc.approve(store, config, motion_id=motion.motion_id, reason="go")

    def test_declaration_version_drift_since_proposal_rejected_with_zero_writes(
        self,
        store: SqliteEventStore,
        config: RuntimeConfig,
        evidence_file: Path,
        tmp_path: Path,
    ) -> None:
        motion = svc.propose(
            store, config, task=OUTBOUND, scope=BLUESKY, to_position="hotl",
            evidence_path=evidence_file, actor="steve",
        )
        config_v2 = _versioned_config(config, tmp_path, version=2)

        with pytest.raises(svc.PaaTransitionError):
            svc.approve(store, config_v2, motion_id=motion.motion_id, reason="go")
        events = store.get_autonomy_events_for_motion(motion.motion_id)
        assert [e.event for e in events] == ["motion_proposed"]

    def test_stale_proposal_rejected_with_zero_partial_writes(
        self,
        store: SqliteEventStore,
        config: RuntimeConfig,
        evidence_file: Path,
        evidence_file_b: Path,
    ) -> None:
        motion_a = svc.propose(
            store, config, task=OUTBOUND, scope=BLUESKY, to_position="hotl",
            evidence_path=evidence_file, actor="steve",
        )
        motion_b = svc.propose(
            store, config, task=OUTBOUND, scope=BLUESKY, to_position="hotl",
            evidence_path=evidence_file_b, actor="steve",
        )
        svc.approve(store, config, motion_id=motion_b.motion_id, reason="go")

        with pytest.raises(svc.PaaTransitionError, match="stale"):
            svc.approve(store, config, motion_id=motion_a.motion_id, reason="go too")
        events_a = store.get_autonomy_events_for_motion(motion_a.motion_id)
        assert [e.event for e in events_a] == ["motion_proposed"]

    def test_stale_proposal_rejected_even_after_position_cycles_back(
        self,
        store: SqliteEventStore,
        config: RuntimeConfig,
        evidence_file: Path,
        evidence_file_b: Path,
    ) -> None:
        """A proposal must bind to the exact position revision it observed,
        not just the position value — otherwise an old pre-incident
        proposal can silently re-promote after an emergency demotion once
        the position happens to cycle back to the same value (A, B
        proposed at hitl; approve B -> hotl; demote -> hitl; A's
        from_position="hitl" would wrongly "match" again)."""
        motion_a = svc.propose(
            store, config, task=OUTBOUND, scope=BLUESKY, to_position="hotl",
            evidence_path=evidence_file, actor="steve",
        )
        motion_b = svc.propose(
            store, config, task=OUTBOUND, scope=BLUESKY, to_position="hotl",
            evidence_path=evidence_file_b, actor="steve",
        )
        svc.approve(store, config, motion_id=motion_b.motion_id, reason="go")
        svc.demote(store, config, task=OUTBOUND, scope=BLUESKY, reason="incident")
        # Position is back to hitl — motion_a's recorded from_position — but
        # a position_changed event (the demotion) happened in between.
        assert (
            svc.resolve_current_position(store, config, OUTBOUND, BLUESKY).current_position
            == "hitl"
        )

        with pytest.raises(svc.PaaTransitionError, match="intervening position change"):
            svc.approve(store, config, motion_id=motion_a.motion_id, reason="late approve")
        events_a = store.get_autonomy_events_for_motion(motion_a.motion_id)
        assert [e.event for e in events_a] == ["motion_proposed"]
        assert (
            svc.resolve_current_position(store, config, OUTBOUND, BLUESKY).current_position
            == "hitl"
        )

    def test_tampered_evidence_rejected_with_zero_writes(
        self, store: SqliteEventStore, config: RuntimeConfig, evidence_file: Path,
        evidence_root: Path,
    ) -> None:
        motion = svc.propose(
            store, config, task=OUTBOUND, scope=BLUESKY, to_position="hotl",
            evidence_path=evidence_file, actor="steve",
        )
        stored = evidence_root / "evidence" / "paa" / motion.evidence_sha256 / "evidence.json"
        stored.write_bytes(b"tampered")

        with pytest.raises(EvidenceError, match="tampered"):
            svc.approve(store, config, motion_id=motion.motion_id, reason="go")
        events = store.get_autonomy_events_for_motion(motion.motion_id)
        assert [e.event for e in events] == ["motion_proposed"]

    def test_partial_history_approved_without_changed_is_corrupt(
        self, store: SqliteEventStore, config: RuntimeConfig, evidence_file: Path,
    ) -> None:
        motion = svc.propose(
            store, config, task=OUTBOUND, scope=BLUESKY, to_position="hotl",
            evidence_path=evidence_file, actor="steve",
        )
        # Bypass the service layer to simulate a partial write that should
        # never happen through propose/approve's own atomic transactions.
        with store.transaction():
            store.insert_autonomy_event(
                event_id="forced-approved", motion_id=motion.motion_id, task=OUTBOUND,
                declaration_version=1, scope=BLUESKY, event="motion_approved",
                from_position="hitl", to_position="hotl",
                evidence_ref=motion.evidence_ref, evidence_sha256=motion.evidence_sha256,
                actor="steve", reason="forced", created_at="2026-01-01T00:00:00.000000Z",
            )

        with pytest.raises(svc.PaaCorruptHistoryError):
            svc.approve(store, config, motion_id=motion.motion_id, reason="go")

    def test_mismatched_identity_between_approved_and_changed_is_corrupt(
        self, store: SqliteEventStore, config: RuntimeConfig, evidence_file: Path,
    ) -> None:
        motion = svc.propose(
            store, config, task=OUTBOUND, scope=BLUESKY, to_position="hotl",
            evidence_path=evidence_file, actor="steve",
        )
        common = dict(
            motion_id=motion.motion_id, task=OUTBOUND, declaration_version=1, scope=BLUESKY,
            evidence_ref=motion.evidence_ref, evidence_sha256=motion.evidence_sha256,
            actor="steve", reason="forced", created_at="2026-01-01T00:00:00.000000Z",
        )
        with store.transaction():
            store.insert_autonomy_event(
                event_id="forced-approved", event="motion_approved",
                from_position="hitl", to_position="hotl", **common,  # type: ignore[arg-type]
            )
            store.insert_autonomy_event(
                event_id="forced-changed", event="position_changed",
                from_position="hitl", to_position="hotl", scope=FARCASTER,  # wrong scope
                motion_id=motion.motion_id, task=OUTBOUND, declaration_version=1,
                evidence_ref=motion.evidence_ref, evidence_sha256=motion.evidence_sha256,
                actor="steve", reason="forced", created_at="2026-01-01T00:00:00.000001Z",
            )

        with pytest.raises(svc.PaaCorruptHistoryError):
            svc.approve(store, config, motion_id=motion.motion_id, reason="go")

    def test_injected_failure_between_approved_and_changed_rolls_back_both(
        self, store: SqliteEventStore, config: RuntimeConfig, evidence_file: Path,
    ) -> None:
        motion = svc.propose(
            store, config, task=OUTBOUND, scope=BLUESKY, to_position="hotl",
            evidence_path=evidence_file, actor="steve",
        )
        original_insert = store.insert_autonomy_event

        def flaky_insert(**kwargs: object) -> None:
            if kwargs.get("event") == "position_changed":
                raise RuntimeError("injected failure")
            original_insert(**kwargs)  # type: ignore[arg-type]

        store.insert_autonomy_event = flaky_insert  # type: ignore[method-assign]
        try:
            with pytest.raises(RuntimeError, match="injected failure"):
                svc.approve(store, config, motion_id=motion.motion_id, reason="go")
        finally:
            store.insert_autonomy_event = original_insert  # type: ignore[method-assign]

        events = store.get_autonomy_events_for_motion(motion.motion_id)
        assert [e.event for e in events] == ["motion_proposed"]
        resolved = svc.resolve_current_position(store, config, OUTBOUND, BLUESKY)
        assert resolved.current_position == "hitl"

    def test_approve_requires_reason(
        self, store: SqliteEventStore, config: RuntimeConfig,
    ) -> None:
        with pytest.raises(svc.PaaValidationError):
            svc.approve(store, config, motion_id="whatever", reason="")


class TestReject:
    def test_reject_unexecuted_proposal(
        self, store: SqliteEventStore, config: RuntimeConfig, evidence_file: Path,
    ) -> None:
        motion = svc.propose(
            store, config, task=OUTBOUND, scope=BLUESKY, to_position="hotl",
            evidence_path=evidence_file, actor="steve",
        )
        rejected = svc.reject(
            store, config, motion_id=motion.motion_id, reason="not ready", actor="ops",
        )
        assert rejected.status == "rejected"
        assert rejected.rejected_by == "ops"
        assert rejected.rejected_reason == "not ready"

    def test_idempotent_rejection(
        self, store: SqliteEventStore, config: RuntimeConfig, evidence_file: Path,
    ) -> None:
        motion = svc.propose(
            store, config, task=OUTBOUND, scope=BLUESKY, to_position="hotl",
            evidence_path=evidence_file, actor="steve",
        )
        first = svc.reject(store, config, motion_id=motion.motion_id, reason="no")
        second = svc.reject(store, config, motion_id=motion.motion_id, reason="no")
        assert first == second

    def test_reject_unknown_motion_not_found(
        self, store: SqliteEventStore, config: RuntimeConfig,
    ) -> None:
        with pytest.raises(svc.PaaNotFoundError):
            svc.reject(store, config, motion_id="nope", reason="no")

    def test_reject_requires_reason(
        self, store: SqliteEventStore, config: RuntimeConfig,
    ) -> None:
        with pytest.raises(svc.PaaValidationError):
            svc.reject(store, config, motion_id="whatever", reason="")

    def test_reject_after_approval_is_terminal(
        self, store: SqliteEventStore, config: RuntimeConfig, evidence_file: Path,
    ) -> None:
        motion = svc.propose(
            store, config, task=OUTBOUND, scope=BLUESKY, to_position="hotl",
            evidence_path=evidence_file, actor="steve",
        )
        svc.approve(store, config, motion_id=motion.motion_id, reason="go")
        with pytest.raises(svc.PaaTerminalError):
            svc.reject(store, config, motion_id=motion.motion_id, reason="too late")

    def test_reject_rejects_never_reopens(
        self, store: SqliteEventStore, config: RuntimeConfig, evidence_file: Path,
    ) -> None:
        motion = svc.propose(
            store, config, task=OUTBOUND, scope=BLUESKY, to_position="hotl",
            evidence_path=evidence_file, actor="steve",
        )
        svc.reject(store, config, motion_id=motion.motion_id, reason="no")
        with pytest.raises(svc.PaaTerminalError):
            svc.approve(store, config, motion_id=motion.motion_id, reason="reconsidering")

    def test_reject_fails_closed_on_matching_rejection_plus_approval(
        self, store: SqliteEventStore, config: RuntimeConfig, evidence_file: Path,
    ) -> None:
        """A rejection that matches the proposal's identity must not be
        treated as a safe idempotent no-op if an approval/change event also
        exists — that combination is corrupt history (rejection alongside
        an approval), not a legitimate repeat rejection, and must fail
        closed rather than silently reporting success."""
        motion = svc.propose(
            store, config, task=OUTBOUND, scope=BLUESKY, to_position="hotl",
            evidence_path=evidence_file, actor="steve",
        )
        common = dict(
            motion_id=motion.motion_id, task=OUTBOUND, declaration_version=1, scope=BLUESKY,
            from_position="hitl", to_position="hotl",
            evidence_ref=motion.evidence_ref, evidence_sha256=motion.evidence_sha256,
            actor="steve", reason="forced",
        )
        with store.transaction():
            store.insert_autonomy_event(
                event_id="forced-rejected", event="motion_rejected",
                created_at="2026-01-01T00:00:00.000000Z", **common,  # type: ignore[arg-type]
            )
            store.insert_autonomy_event(
                event_id="forced-approved", event="motion_approved",
                created_at="2026-01-01T00:00:00.000001Z", **common,  # type: ignore[arg-type]
            )

        with pytest.raises(svc.PaaCorruptHistoryError):
            svc.reject(store, config, motion_id=motion.motion_id, reason="no")


class TestDemote:
    def test_demote_atomically_executes(
        self, store: SqliteEventStore, config: RuntimeConfig, evidence_root: Path,
    ) -> None:
        # Move to hotl first, so there is something to demote from.
        evidence = evidence_root.parent / "promo.json"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text('{"report": "promo"}')
        motion = svc.propose(
            store, config, task=OUTBOUND, scope=BLUESKY, to_position="hotl",
            evidence_path=evidence, actor="steve",
        )
        svc.approve(store, config, motion_id=motion.motion_id, reason="go")

        demotion = svc.demote(
            store, config, task=OUTBOUND, scope=BLUESKY, reason="incident",
            actor="oncall", source_rows=["posts:5", "posts:2", "posts:5"],
        )
        assert demotion.status == "executed"
        assert demotion.from_position == "hotl"
        assert demotion.to_position == "hitl"
        events = store.get_autonomy_events_for_motion(demotion.motion_id)
        assert [e.event for e in events] == [
            "motion_proposed", "motion_approved", "position_changed",
        ]

        resolved = svc.resolve_current_position(store, config, OUTBOUND, BLUESKY)
        assert resolved.current_position == "hitl"

    def test_demote_requires_reason(
        self, store: SqliteEventStore, config: RuntimeConfig,
    ) -> None:
        with pytest.raises(svc.PaaValidationError):
            svc.demote(store, config, task=OUTBOUND, scope=BLUESKY, reason="")

    def test_demote_fails_when_not_at_declared_demotion_source(
        self, store: SqliteEventStore, config: RuntimeConfig,
    ) -> None:
        # Still at hitl — declared demotion is hotl->hitl.
        with pytest.raises(svc.PaaTransitionError):
            svc.demote(store, config, task=OUTBOUND, scope=BLUESKY, reason="incident")

    def test_demote_evidence_is_canonical_json_with_sorted_unique_source_rows(
        self, store: SqliteEventStore, config: RuntimeConfig, evidence_root: Path,
    ) -> None:
        evidence = evidence_root.parent / "promo.json"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text('{"report": "promo"}')
        motion = svc.propose(
            store, config, task=OUTBOUND, scope=BLUESKY, to_position="hotl",
            evidence_path=evidence, actor="steve",
        )
        svc.approve(store, config, motion_id=motion.motion_id, reason="go")

        demotion = svc.demote(
            store, config, task=OUTBOUND, scope=BLUESKY, reason="incident", actor="oncall",
            source_rows=["posts:5", "posts:2", "posts:5"],
        )
        stored = (
            evidence_root / "evidence" / "paa" / demotion.evidence_sha256 / "evidence.json"
        )
        payload = json.loads(stored.read_text())
        assert payload["source_rows"] == ["posts:2", "posts:5"]
        assert payload["task"] == OUTBOUND
        assert payload["scope"] == BLUESKY
        assert payload["reason"] == "incident"
        assert payload["actor"] == "oncall"


class TestShow:
    def test_show_returns_declaration_and_resolved_position(
        self, store: SqliteEventStore, config: RuntimeConfig,
    ) -> None:
        result = svc.show(store, config, task=OUTBOUND, scope=BLUESKY)
        assert result == {
            "task": OUTBOUND,
            "declaration_version": 1,
            "deployment": "active",
            "initial_position": "hitl",
            "scope": BLUESKY,
            "current_position": "hitl",
            "latest_position_event": None,
        }


class TestListMotions:
    def test_status_precedence_and_aggregation(
        self, store: SqliteEventStore, config: RuntimeConfig,
        evidence_file: Path, evidence_file_b: Path,
    ) -> None:
        proposed_only = svc.propose(
            store, config, task=OUTBOUND, scope=BLUESKY, to_position="hotl",
            evidence_path=evidence_file, actor="steve",
        )
        rejected = svc.propose(
            store, config, task=INBOUND, scope=None, to_position="hotl",
            evidence_path=evidence_file, actor="steve",
        )
        svc.reject(store, config, motion_id=rejected.motion_id, reason="no")

        executed = svc.propose(
            store, config, task=OUTBOUND, scope=FARCASTER, to_position="hotl",
            evidence_path=evidence_file_b, actor="steve",
        )
        svc.approve(store, config, motion_id=executed.motion_id, reason="go")

        all_motions = svc.list_motions(store)
        by_id = {m.motion_id: m for m in all_motions}
        assert by_id[proposed_only.motion_id].status == "proposed"
        assert by_id[rejected.motion_id].status == "rejected"
        assert by_id[executed.motion_id].status == "executed"

        only_rejected = svc.list_motions(store, status="rejected")
        assert [m.motion_id for m in only_rejected] == [rejected.motion_id]

        only_outbound = svc.list_motions(store, task=OUTBOUND)
        assert {m.motion_id for m in only_outbound} == {
            proposed_only.motion_id, executed.motion_id,
        }

    def test_sorted_by_proposed_at_then_motion_id(
        self, store: SqliteEventStore, config: RuntimeConfig,
    ) -> None:
        common = dict(
            task=OUTBOUND, declaration_version=1, scope=BLUESKY, event="motion_proposed",
            from_position="hitl", to_position="hotl",
            evidence_ref="evidence/paa/" + "a" * 64 + "/evidence.json",
            evidence_sha256="a" * 64, actor="steve", reason="r",
        )
        with store.transaction():
            store.insert_autonomy_event(
                event_id="e1", motion_id="m-b", created_at="2026-01-01T00:00:00.000000Z",
                **common,  # type: ignore[arg-type]
            )
            store.insert_autonomy_event(
                event_id="e2", motion_id="m-a", created_at="2026-01-01T00:00:00.000000Z",
                **common,  # type: ignore[arg-type]
            )
            store.insert_autonomy_event(
                event_id="e3", motion_id="m-z", created_at="2025-12-31T00:00:00.000000Z",
                **common,  # type: ignore[arg-type]
            )

        motions = svc.list_motions(store)
        assert [m.motion_id for m in motions] == ["m-z", "m-a", "m-b"]

    def test_invalid_status_filter_rejected(
        self, store: SqliteEventStore, config: RuntimeConfig,
    ) -> None:
        with pytest.raises(svc.PaaValidationError):
            svc.list_motions(store, status="not_a_status")


class TestResolveActorFallbackFailure:
    """The OS-login fallback is an I/O boundary and converts to a domain error.

    getpass.getuser() consults the environment and then the password
    database. A container or daemon with no passwd entry has neither:
    Python 3.13+ raises OSError there, 3.12 raises KeyError. Both must
    surface as the error that tells the operator what to do, not as a
    traceback from a stdlib module they never called.
    """

    @pytest.mark.parametrize(
        "raised",
        [OSError("no login name"), KeyError("getpwuid()")],
        ids=["oserror", "keyerror"],
    )
    def test_login_lookup_failure_becomes_validation_error(
        self, monkeypatch: pytest.MonkeyPatch, raised: Exception,
    ) -> None:
        monkeypatch.delenv("CUSTOM_PAA_ACTOR", raising=False)

        def _fail() -> str:
            raise raised

        monkeypatch.setattr(svc.getpass, "getuser", _fail)
        with pytest.raises(svc.PaaValidationError, match="CUSTOM_PAA_ACTOR"):
            svc.resolve_actor(None, env_var="CUSTOM_PAA_ACTOR")

    def test_explicit_actor_never_reaches_the_fallback(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def _fail() -> str:
            raise OSError("must not be called")

        monkeypatch.setattr(svc.getpass, "getuser", _fail)
        assert svc.resolve_actor("steve", env_var="CUSTOM_PAA_ACTOR") == "steve"


class TestListMotionsCorruptHistory:
    def test_error_names_every_corrupt_motion(
        self, store: SqliteEventStore, config: RuntimeConfig,
    ) -> None:
        """`list` is the command an operator reaches for when history is
        suspect, so its failure has to name what to inspect — all of it,
        not just whichever group happened to be projected first."""
        common = dict(
            task=OUTBOUND, declaration_version=1, scope=BLUESKY, event="motion_approved",
            from_position="hitl", to_position="hotl",
            evidence_ref="evidence/paa/" + "a" * 64 + "/evidence.json",
            evidence_sha256="a" * 64, actor="steve", reason="r",
        )
        with store.transaction():
            store.insert_autonomy_event(
                event_id="e1", motion_id="m-zeta", created_at="2026-01-01T00:00:00.000000Z",
                **common,  # type: ignore[arg-type]
            )
            store.insert_autonomy_event(
                event_id="e2", motion_id="m-alpha", created_at="2026-01-02T00:00:00.000000Z",
                **common,  # type: ignore[arg-type]
            )

        with pytest.raises(svc.PaaCorruptHistoryError) as excinfo:
            svc.list_motions(store)
        message = str(excinfo.value)
        assert "m-alpha" in message
        assert "m-zeta" in message
        assert message.index("m-alpha") < message.index("m-zeta"), "ids are sorted"

    def test_a_corrupt_motion_still_fails_the_listing(
        self, store: SqliteEventStore, config: RuntimeConfig, evidence_file: Path,
    ) -> None:
        """Fail-closed is preserved: a listing that silently omitted the
        motions it could not project would be a worse answer than none."""
        svc.propose(
            store, config, task=OUTBOUND, scope=BLUESKY, to_position="hotl",
            evidence_path=evidence_file, actor="steve",
        )
        with store.transaction():
            store.insert_autonomy_event(
                event_id="orphan", motion_id="m-orphan", task=OUTBOUND,
                declaration_version=1, scope=BLUESKY, event="motion_approved",
                from_position="hitl", to_position="hotl",
                evidence_ref="evidence/paa/" + "a" * 64 + "/evidence.json",
                evidence_sha256="a" * 64, actor="steve", reason="r",
                created_at="2026-01-01T00:00:00.000000Z",
            )

        with pytest.raises(svc.PaaCorruptHistoryError, match="m-orphan"):
            svc.list_motions(store)


class TestProposeResolvesUnderTheWriteLock:
    """B7: the position is folded inside the transaction that records it.

    Reading it beforehand leaves a window as wide as the evidence I/O, and
    a position_changed committed in that window is not merely missed — it
    is unrecoverable later. approve()'s intervening-change check anchors
    its baseline to the proposal's own (created_at, id), so a change that
    landed *before* the insert is absorbed into the baseline rather than
    detected as intervening: baseline and latest agree and the stale
    proposal passes.
    """

    def test_position_change_during_evidence_io_is_not_missed(
        self,
        store: SqliteEventStore,
        config: RuntimeConfig,
        evidence_file: Path,
        evidence_file_b: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        first = svc.propose(
            store, config, task=OUTBOUND, scope=BLUESKY, to_position="hotl",
            evidence_path=evidence_file, actor="steve",
        )

        real_store_evidence = svc.store_evidence
        calls: list[str] = []

        def _approve_mid_flight(data: bytes, *, root: Path) -> tuple[str, str]:
            """Stand in for the window: commit a position change while the
            second proposal is doing its evidence I/O."""
            calls.append("raced")
            svc.approve(store, config, motion_id=first.motion_id, reason="emergency")
            return real_store_evidence(data, root=root)

        monkeypatch.setattr(svc, "store_evidence", _approve_mid_flight)

        # The position is hitl when the second proposal starts and hotl by
        # the time it inserts. hotl -> hotl is not a declared transition,
        # so resolving under the lock rejects it. Resolving beforehand
        # would have recorded from_position=hitl as fact.
        with pytest.raises(svc.PaaTransitionError):
            svc.propose(
                store, config, task=OUTBOUND, scope=BLUESKY, to_position="hotl",
                evidence_path=evidence_file_b, actor="steve",
            )

        assert calls == ["raced"], "the race must actually have been exercised"

    def test_no_motion_is_recorded_when_the_lock_rejects(
        self,
        store: SqliteEventStore,
        config: RuntimeConfig,
        evidence_file: Path,
        evidence_file_b: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        first = svc.propose(
            store, config, task=OUTBOUND, scope=BLUESKY, to_position="hotl",
            evidence_path=evidence_file, actor="steve",
        )
        real_store_evidence = svc.store_evidence

        def _approve_mid_flight(data: bytes, *, root: Path) -> tuple[str, str]:
            svc.approve(store, config, motion_id=first.motion_id, reason="emergency")
            return real_store_evidence(data, root=root)

        monkeypatch.setattr(svc, "store_evidence", _approve_mid_flight)
        with pytest.raises(svc.PaaTransitionError):
            svc.propose(
                store, config, task=OUTBOUND, scope=BLUESKY, to_position="hotl",
                evidence_path=evidence_file_b, actor="steve",
            )

        # Only the first motion exists: the rejected proposal wrote nothing.
        assert [m.motion_id for m in svc.list_motions(store)] == [first.motion_id]

    def test_uncontended_proposal_still_records_the_current_position(
        self, store: SqliteEventStore, config: RuntimeConfig, evidence_file: Path,
    ) -> None:
        """The lock changes when the fold happens, not what it produces."""
        motion = svc.propose(
            store, config, task=OUTBOUND, scope=BLUESKY, to_position="hotl",
            evidence_path=evidence_file, actor="steve",
        )
        assert motion.from_position == "hitl"
        assert motion.to_position == "hotl"
