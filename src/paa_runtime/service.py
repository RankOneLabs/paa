"""PAA autonomy control-plane workflows: propose, approve, reject, demote,
resolve, show, and list — built entirely on paa_runtime.declarations (task
declarations), paa_runtime.events (typed vocabulary), paa_runtime.evidence
(content-addressed storage), and EventStore's narrow autonomy_events
insert/read surface.

Current autonomy position is never stored: `resolve_current_position`
always folds it fresh from the declaration's initial_position plus the
latest exact-scope `position_changed` event. Every write here opens its
own `store.transaction()` / `store.begin_immediate()` — this module owns
transaction *boundaries*, the store owns the transaction *mechanics*.
"""

from __future__ import annotations

import getpass
import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from paa_runtime.config import RuntimeConfig
from paa_runtime.declarations import (
    AutonomyPosition,
    PaaTaskDeclaration,
    get_paa_declaration,
)
from paa_runtime.events import (
    AUTONOMY_POSITIONS,
    MOTION_STATUSES,
    MotionStatus,
    new_created_at,
    new_event_id,
    new_motion_id,
)
from paa_runtime.evidence import read_evidence_bytes, store_evidence, verify_evidence
from paa_runtime.store import AutonomyEvent, EventStore

# The proposal fields every later event (approved/rejected/changed) must
# copy verbatim — used both to build sibling events and to detect a
# corrupt or stale history that no longer agrees with the proposal.
_IDENTITY_FIELDS: tuple[str, ...] = (
    "task", "declaration_version", "scope", "from_position", "to_position",
    "evidence_ref", "evidence_sha256",
)


class PaaServiceError(RuntimeError):
    """Base class for every PAA workflow failure. Callers (the CLI) treat
    any instance as an actionable error: print str(exc) to stderr and
    exit nonzero."""


class PaaNotFoundError(PaaServiceError):
    """No motion, proposal, or resolvable declaration matches the request."""


class PaaValidationError(PaaServiceError):
    """A request argument (scope, position, reason, evidence) is invalid."""


class PaaTransitionError(PaaServiceError):
    """The requested or proposal-recorded transition is not the
    declaration's exact promotion or demotion from the resolved position,
    or the declaration has changed version since the proposal was made."""


class PaaCorruptHistoryError(PaaServiceError):
    """A motion's event history is internally inconsistent — partial
    approval, mismatched identity between sibling events, or a
    rejection alongside an approval. Never resolved automatically."""


class PaaTerminalError(PaaServiceError):
    """The motion is in a terminal state that forbids the requested
    operation: approving a rejected motion, or rejecting an
    approved/executed one."""


@dataclass(frozen=True, slots=True)
class ResolvedPosition:
    """The exact-scope current autonomy position, folded from the
    declaration's initial_position plus the latest matching
    position_changed event (or the declaration alone if none exists)."""

    task: str
    declaration_version: int
    deployment: str
    initial_position: AutonomyPosition
    scope: str | None
    current_position: AutonomyPosition
    latest_position_event: dict[str, Any] | None


PositionSource = Literal["declaration_initial", "position_event"]


@dataclass(frozen=True, slots=True)
class PositionResolution:
    """A governed effect's runtime authorization snapshot for one attempt.

    Narrower than ResolvedPosition: only the fields a runtime enforcement
    point (content_publishing.publish_candidate) needs to attach to its
    verifier evidence and claimed publication so an audit can reconstruct
    *why* a position was in force for that exact attempt — declaration's
    initial_position when no position_changed event has ever fired for
    this exact (task, declaration_version, scope), or the exact event that
    last changed it.
    """

    task: str
    declaration_version: int
    deployment: str
    scope: str | None
    position: AutonomyPosition
    source: PositionSource
    source_event_id: str | None


def to_position_resolution(resolved: ResolvedPosition) -> PositionResolution:
    """Narrow a ResolvedPosition down to one attempt's PositionResolution."""
    latest = resolved.latest_position_event
    return PositionResolution(
        task=resolved.task,
        declaration_version=resolved.declaration_version,
        deployment=resolved.deployment,
        scope=resolved.scope,
        position=resolved.current_position,
        source="position_event" if latest is not None else "declaration_initial",
        source_event_id=latest["id"] if latest is not None else None,
    )


def resolve_position(
    store: EventStore,
    config: RuntimeConfig,
    task: str,
    scope: str | None,
) -> PositionResolution:
    """Resolve one attempt's exact-scope PositionResolution.

    A thin wrapper over resolve_current_position for runtime enforcement
    callers that need the narrower audit-oriented shape rather than the
    full ResolvedPosition (which `show` and the CLI still use directly).
    Safe to call inside a caller's own transaction, same as
    resolve_current_position.
    """
    resolved = resolve_current_position(store, config, task, scope)
    return to_position_resolution(resolved)


@dataclass(frozen=True, slots=True)
class Motion:
    """The aggregated, derived view of one motion's 1-4 events. Status is
    always a projection of the underlying events, never stored."""

    motion_id: str
    task: str
    declaration_version: int
    scope: str | None
    from_position: AutonomyPosition
    to_position: AutonomyPosition
    status: MotionStatus
    evidence_ref: str
    evidence_sha256: str
    proposed_at: str
    proposed_by: str
    proposed_reason: str
    approved_at: str | None
    approved_by: str | None
    approved_reason: str | None
    rejected_at: str | None
    rejected_by: str | None
    rejected_reason: str | None
    executed_at: str | None

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "motion_id": self.motion_id,
            "task": self.task,
            "declaration_version": self.declaration_version,
            "scope": self.scope,
            "from_position": self.from_position,
            "to_position": self.to_position,
            "status": self.status,
            "evidence_ref": self.evidence_ref,
            "evidence_sha256": self.evidence_sha256,
            "proposed_at": self.proposed_at,
            "proposed_by": self.proposed_by,
            "proposed_reason": self.proposed_reason,
            "approved_at": self.approved_at,
            "approved_by": self.approved_by,
            "approved_reason": self.approved_reason,
            "rejected_at": self.rejected_at,
            "rejected_by": self.rejected_by,
            "rejected_reason": self.rejected_reason,
            "executed_at": self.executed_at,
        }


def resolve_actor(explicit: str | None, *, env_var: str) -> str:
    """--actor, then the configured env var, then the OS login name.

    The last fallback is an I/O boundary: ``getpass.getuser()`` consults
    the environment and then the password database, and in a container or
    a daemon with no passwd entry it has neither to consult. Python 3.13+
    raises OSError there, 3.12 raises KeyError. Either way the operator's
    fix is the same — pass --actor or set the env var — so both convert
    to the error that says so, rather than escaping as a stack trace from
    a stdlib module the caller never named.
    """
    if explicit:
        return explicit
    env_actor = os.environ.get(env_var)
    if env_actor:
        return env_actor
    try:
        return getpass.getuser()
    except (OSError, KeyError) as exc:
        raise PaaValidationError(
            "could not determine the acting operator: no explicit actor, "
            f"no {env_var} in the environment, and the OS login name is "
            f"unavailable ({exc!r}). Pass an explicit actor or set {env_var}."
        ) from exc


def _validate_scope(declaration: PaaTaskDeclaration, scope: str | None) -> None:
    if declaration.scopes is None:
        if scope is not None:
            raise PaaValidationError(
                f"task {declaration.task!r} declares no scopes; scope must be None, "
                f"got {scope!r}"
            )
        return
    if scope not in declaration.scopes:
        raise PaaValidationError(
            f"scope {scope!r} is not permitted for task {declaration.task!r}; must be one of "
            f"{sorted(declaration.scopes)}"
        )


def _require_declared_transition(
    declaration: PaaTaskDeclaration, from_position: str, to_position: str,
) -> None:
    promotion, demotion = declaration.promotion, declaration.demotion
    if from_position == promotion.from_position and to_position == promotion.to_position:
        return
    if from_position == demotion.from_position and to_position == demotion.to_position:
        return
    raise PaaTransitionError(
        f"{declaration.task!r} at declaration version {declaration.version} declares no "
        f"transition from {from_position!r} to {to_position!r} (only promotion "
        f"{promotion.from_position}->{promotion.to_position} or demotion "
        f"{demotion.from_position}->{demotion.to_position})"
    )


def _fold_current_position(
    store: EventStore, declaration: PaaTaskDeclaration, scope: str | None,
) -> tuple[AutonomyPosition, AutonomyEvent | None]:
    """The current position for one exact (task, version, scope) triple,
    folded from the latest position_changed event or, if authority has
    never moved, the declaration's initial_position.

    One read query and no file I/O, which is what makes it callable with
    a write lock already held — see ``propose``, where the fold has to
    happen inside the transaction that records its result.
    """
    latest = store.get_latest_position_changed_event(
        task=declaration.task, declaration_version=declaration.version, scope=scope,
    )
    current: AutonomyPosition = (
        latest.to_position if latest is not None else declaration.initial_position
    )
    return current, latest


def resolve_current_position(
    store: EventStore,
    config: RuntimeConfig,
    task: str,
    scope: str | None,
) -> ResolvedPosition:
    """Fold the exact-scope current position from the declaration plus the
    latest matching position_changed event. A single read query — safe to
    call standalone or nested inside a caller's own transaction.
    """
    declaration = get_paa_declaration(
        task, directory=config.declarations_dir, registry=config.registry,
    )
    _validate_scope(declaration, scope)
    current, latest = _fold_current_position(store, declaration, scope)
    return ResolvedPosition(
        task=declaration.task,
        declaration_version=declaration.version,
        deployment=declaration.deployment,
        initial_position=declaration.initial_position,
        scope=scope,
        current_position=current,
        latest_position_event=latest.to_json_dict() if latest is not None else None,
    )


def _same_identity(a: AutonomyEvent, b: AutonomyEvent) -> bool:
    return all(getattr(a, field) == getattr(b, field) for field in _IDENTITY_FIELDS)


def _project_motion(events: Sequence[AutonomyEvent]) -> Motion:
    by_event: dict[str, AutonomyEvent] = {row.event: row for row in events}

    proposed = by_event.get("motion_proposed")
    if proposed is None:
        raise PaaCorruptHistoryError("motion has no motion_proposed event to project from")
    approved = by_event.get("motion_approved")
    rejected = by_event.get("motion_rejected")
    changed = by_event.get("position_changed")

    status: MotionStatus
    if changed is not None:
        status = "executed"
    elif rejected is not None:
        status = "rejected"
    elif approved is not None:
        status = "approved"
    else:
        status = "proposed"

    return Motion(
        motion_id=proposed.motion_id,
        task=proposed.task,
        declaration_version=proposed.declaration_version,
        scope=proposed.scope,
        from_position=proposed.from_position,
        to_position=proposed.to_position,
        status=status,
        evidence_ref=proposed.evidence_ref,
        evidence_sha256=proposed.evidence_sha256,
        proposed_at=proposed.created_at,
        proposed_by=proposed.actor,
        proposed_reason=proposed.reason,
        approved_at=approved.created_at if approved else None,
        approved_by=approved.actor if approved else None,
        approved_reason=approved.reason if approved else None,
        rejected_at=rejected.created_at if rejected else None,
        rejected_by=rejected.actor if rejected else None,
        rejected_reason=rejected.reason if rejected else None,
        executed_at=changed.created_at if changed else None,
    )


def propose(
    store: EventStore,
    config: RuntimeConfig,
    *,
    task: str,
    scope: str | None,
    to_position: str,
    evidence_path: Path,
    actor: str | None = None,
    reason: str | None = None,
) -> Motion:
    """Insert exactly one motion_proposed event. Has zero effect on the
    resolved current position until an operator approves it."""
    declaration = get_paa_declaration(
        task, directory=config.declarations_dir, registry=config.registry,
    )
    _validate_scope(declaration, scope)
    if to_position not in AUTONOMY_POSITIONS:
        raise PaaValidationError(
            f"to_position {to_position!r} is not a valid autonomy position "
            f"(must be one of {sorted(AUTONOMY_POSITIONS)})"
        )
    # Advisory pre-check. Not authoritative — the position can move
    # between here and the insert, which is exactly why the same check
    # runs again under the write lock below. Its only job is to fail an
    # obviously undeclared transition before evidence is written, so the
    # common operator error doesn't leave an orphan artifact in the store.
    provisional_from, _ = _fold_current_position(store, declaration, scope)
    _require_declared_transition(declaration, provisional_from, to_position)

    data = read_evidence_bytes(evidence_path)
    if not data:
        raise PaaValidationError(f"evidence file is empty: {evidence_path}")
    evidence_ref, evidence_sha256 = store_evidence(data, root=config.evidence_root)

    resolved_actor = resolve_actor(actor, env_var=config.actor_env_var)
    motion_id = new_motion_id()

    # The position is resolved inside the transaction that records the
    # proposal, under an immediate write lock, so no position_changed can
    # commit between the read and the insert.
    #
    # Reading it outside would leave a window as wide as the evidence I/O
    # above — tens of milliseconds — and a demotion landing in that window
    # would be recorded as never having happened: from_position would hold
    # the pre-demotion value as fact. approve() could not catch it either.
    # Its intervening-change check anchors the baseline to the proposal's
    # own (created_at, id), so a position_changed committed *before* the
    # insert is absorbed into that baseline rather than detected as
    # intervening — baseline and latest agree, and the stale proposal
    # passes. The guard defends proposal-to-approval; this closes
    # read-to-insert.
    with store.begin_immediate():
        from_position, _ = _fold_current_position(store, declaration, scope)
        _require_declared_transition(declaration, from_position, to_position)
        resolved_reason = reason or f"requested transition {from_position} to {to_position}"
        store.insert_autonomy_event(
            event_id=new_event_id(),
            motion_id=motion_id,
            task=declaration.task,
            declaration_version=declaration.version,
            scope=scope,
            event="motion_proposed",
            from_position=from_position,
            to_position=to_position,  # type: ignore[arg-type]
            evidence_ref=evidence_ref,
            evidence_sha256=evidence_sha256,
            actor=resolved_actor,
            reason=resolved_reason,
            created_at=new_created_at(),
        )
        events = store.get_autonomy_events_for_motion(motion_id)

    return _project_motion(events)


def approve(
    store: EventStore,
    config: RuntimeConfig,
    *,
    motion_id: str,
    reason: str,
    actor: str | None = None,
) -> Motion:
    """Approve and atomically execute a proposal: motion_approved and
    position_changed are inserted together inside one begin_immediate
    transaction, so authorization and effect either both commit or
    neither does."""
    if not reason:
        raise PaaValidationError("--reason is required to approve a motion")
    resolved_actor = resolve_actor(actor, env_var=config.actor_env_var)

    with store.begin_immediate():
        events = store.get_autonomy_events_for_motion(motion_id)
        by_event = {row.event: row for row in events}
        proposed = by_event.get("motion_proposed")
        if proposed is None:
            raise PaaNotFoundError(f"no proposal found for motion_id {motion_id!r}")

        rejected = by_event.get("motion_rejected")
        approved = by_event.get("motion_approved")
        changed = by_event.get("position_changed")

        if rejected is not None:
            if approved is not None or changed is not None:
                raise PaaCorruptHistoryError(
                    f"motion {motion_id!r} has both a rejection and an approval event"
                )
            raise PaaTerminalError(f"motion {motion_id!r} was rejected and cannot be approved")

        if approved is not None and changed is not None:
            if _same_identity(approved, proposed) and _same_identity(changed, proposed):
                return _project_motion(events)
            raise PaaCorruptHistoryError(
                f"motion {motion_id!r} has approved/changed events that do not match "
                "its proposal identity"
            )
        if approved is not None or changed is not None:
            raise PaaCorruptHistoryError(
                f"motion {motion_id!r} has a partial approval history: "
                f"{'motion_approved' if approved else 'position_changed'} exists without "
                f"its sibling event"
            )

        # Fresh approval path — nothing has been written for this motion yet.
        verify_evidence(proposed.evidence_ref, proposed.evidence_sha256, root=config.evidence_root)

        declaration = get_paa_declaration(
            proposed.task, directory=config.declarations_dir, registry=config.registry,
        )
        if declaration.version != proposed.declaration_version:
            raise PaaTransitionError(
                f"declaration version for {proposed.task!r} has changed since this "
                f"proposal was made ({proposed.declaration_version} -> "
                f"{declaration.version}); propose a new motion against the current declaration"
            )
        _require_declared_transition(
            declaration, proposed.from_position, proposed.to_position
        )

        # A value-equality check ("current position == from_position") is not
        # enough: position can cycle back to the same value through an
        # intervening demotion/promotion (A, B proposed at hitl; approve B
        # -> hotl; demote -> hitl; A's from_position=hitl still "matches"
        # even though A's view of the world is stale). Instead, require
        # that no position_changed event has been inserted since this
        # proposal was made — i.e. the position_changed event that was
        # latest just before the proposal (or none) must still be latest now.
        baseline = store.get_position_changed_event_before(
            task=proposed.task, declaration_version=proposed.declaration_version,
            scope=proposed.scope, created_at=proposed.created_at, event_id=proposed.id,
        )
        latest_now = store.get_latest_position_changed_event(
            task=proposed.task, declaration_version=proposed.declaration_version,
            scope=proposed.scope,
        )
        baseline_id = baseline.id if baseline is not None else None
        latest_now_id = latest_now.id if latest_now is not None else None
        if baseline_id != latest_now_id:
            raise PaaTransitionError(
                f"an intervening position change occurred for task {proposed.task!r} "
                f"scope {proposed.scope!r} since this proposal was made — stale proposal"
            )

        for event_kind in ("motion_approved", "position_changed"):
            store.insert_autonomy_event(
                event_id=new_event_id(),
                motion_id=motion_id,
                task=proposed.task,
                declaration_version=proposed.declaration_version,
                scope=proposed.scope,
                event=event_kind,
                from_position=proposed.from_position,
                to_position=proposed.to_position,
                evidence_ref=proposed.evidence_ref,
                evidence_sha256=proposed.evidence_sha256,
                actor=resolved_actor,
                reason=reason,
                created_at=new_created_at(),
            )

        events = store.get_autonomy_events_for_motion(motion_id)

    return _project_motion(events)


def reject(
    store: EventStore,
    config: RuntimeConfig,
    *,
    motion_id: str,
    reason: str,
    actor: str | None = None,
) -> Motion:
    """Reject an existing, unexecuted proposal. Idempotent when a matching
    rejection already exists; terminal once the motion has been approved
    or executed."""
    if not reason:
        raise PaaValidationError("--reason is required to reject a motion")
    resolved_actor = resolve_actor(actor, env_var=config.actor_env_var)

    with store.begin_immediate():
        events = store.get_autonomy_events_for_motion(motion_id)
        by_event = {row.event: row for row in events}
        proposed = by_event.get("motion_proposed")
        if proposed is None:
            raise PaaNotFoundError(f"no proposal found for motion_id {motion_id!r}")

        rejected = by_event.get("motion_rejected")
        approved = by_event.get("motion_approved")
        changed = by_event.get("position_changed")

        if rejected is not None:
            if approved is not None or changed is not None:
                raise PaaCorruptHistoryError(
                    f"motion {motion_id!r} has both a rejection and an approval/change event"
                )
            if _same_identity(rejected, proposed):
                return _project_motion(events)
            raise PaaCorruptHistoryError(
                f"motion {motion_id!r} has a rejection that does not match its proposal identity"
            )

        if approved is not None or changed is not None:
            raise PaaTerminalError(
                f"motion {motion_id!r} is already approved or executed and cannot be rejected"
            )

        store.insert_autonomy_event(
            event_id=new_event_id(),
            motion_id=motion_id,
            task=proposed.task,
            declaration_version=proposed.declaration_version,
            scope=proposed.scope,
            event="motion_rejected",
            from_position=proposed.from_position,
            to_position=proposed.to_position,
            evidence_ref=proposed.evidence_ref,
            evidence_sha256=proposed.evidence_sha256,
            actor=resolved_actor,
            reason=reason,
            created_at=new_created_at(),
        )
        events = store.get_autonomy_events_for_motion(motion_id)

    return _project_motion(events)


def _canonical_demotion_evidence(
    *,
    task: str,
    declaration_version: int,
    scope: str | None,
    actor: str,
    reason: str,
    source_rows: Sequence[str],
    generated_at: str,
) -> bytes:
    payload = {
        "task": task,
        "declaration_version": declaration_version,
        "scope": scope,
        "actor": actor,
        "reason": reason,
        "source_rows": sorted(set(source_rows)),
        "generated_at": generated_at,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def demote(
    store: EventStore,
    config: RuntimeConfig,
    *,
    task: str,
    scope: str | None,
    reason: str,
    actor: str | None = None,
    source_rows: Sequence[str] = (),
) -> Motion:
    """One-command emergency demotion: proposed, approved, and
    position_changed are inserted together inside one begin_immediate
    transaction, generating and content-addressing their own evidence."""
    if not reason:
        raise PaaValidationError("--reason is required to demote")
    declaration = get_paa_declaration(
        task, directory=config.declarations_dir, registry=config.registry,
    )
    _validate_scope(declaration, scope)
    resolved_actor = resolve_actor(actor, env_var=config.actor_env_var)

    with store.begin_immediate():
        resolved = resolve_current_position(store, config, task, scope)
        if resolved.current_position != declaration.demotion.from_position:
            raise PaaTransitionError(
                f"cannot demote {task!r} scope {scope!r}: current position "
                f"{resolved.current_position!r} is not the declared demotion source "
                f"{declaration.demotion.from_position!r}"
            )
        to_position = declaration.demotion.to_position

        generated_at = new_created_at()
        evidence_bytes = _canonical_demotion_evidence(
            task=declaration.task,
            declaration_version=declaration.version,
            scope=scope,
            actor=resolved_actor,
            reason=reason,
            source_rows=source_rows,
            generated_at=generated_at,
        )
        evidence_ref, evidence_sha256 = store_evidence(evidence_bytes, root=config.evidence_root)

        motion_id = new_motion_id()
        for event_kind in ("motion_proposed", "motion_approved", "position_changed"):
            store.insert_autonomy_event(
                event_id=new_event_id(),
                motion_id=motion_id,
                task=declaration.task,
                declaration_version=declaration.version,
                scope=scope,
                event=event_kind,
                from_position=resolved.current_position,
                to_position=to_position,
                evidence_ref=evidence_ref,
                evidence_sha256=evidence_sha256,
                actor=resolved_actor,
                reason=reason,
                created_at=new_created_at(),
            )

        events = store.get_autonomy_events_for_motion(motion_id)

    return _project_motion(events)


def show(
    store: EventStore,
    config: RuntimeConfig,
    *,
    task: str,
    scope: str | None,
) -> dict[str, Any]:
    """The declaration/scope/current-position surface a consumer's
    `show` command renders."""
    resolved = resolve_current_position(store, config, task, scope)
    return {
        "task": resolved.task,
        "declaration_version": resolved.declaration_version,
        "deployment": resolved.deployment,
        "initial_position": resolved.initial_position,
        "scope": resolved.scope,
        "current_position": resolved.current_position,
        "latest_position_event": resolved.latest_position_event,
    }


def list_motions(
    store: EventStore, *, status: str | None = None, task: str | None = None,
) -> list[Motion]:
    """Every motion, derived from event history and sorted by proposal
    created_at then motion_id. Optionally filtered by derived status
    and/or task."""
    if status is not None and status not in MOTION_STATUSES:
        raise PaaValidationError(
            f"status {status!r} is not a valid motion status "
            f"(must be one of {sorted(MOTION_STATUSES)})"
        )
    events = store.get_autonomy_events(task=task)
    grouped: dict[str, list[AutonomyEvent]] = {}
    for row in events:
        grouped.setdefault(row.motion_id, []).append(row)

    # Project every group before failing, so the error can name all the
    # corrupt motions rather than only the first one encountered. `list`
    # is the command an operator reaches for *when* history is suspect,
    # so its failure has to be the diagnostic: it still fails closed —
    # a listing that silently omitted unprojectable motions would be a
    # worse answer than no listing — but it fails with the ids to inspect.
    motions: list[Motion] = []
    corrupt: list[str] = []
    for motion_id, rows in grouped.items():
        try:
            motions.append(_project_motion(rows))
        except PaaCorruptHistoryError:
            corrupt.append(motion_id)
    if corrupt:
        raise PaaCorruptHistoryError(
            f"motions have no motion_proposed event: {sorted(corrupt)}"
        )

    if status is not None:
        motions = [m for m in motions if m.status == status]
    motions.sort(key=lambda m: (m.proposed_at, m.motion_id))
    return motions


__all__ = [
    "Motion",
    "PaaCorruptHistoryError",
    "PaaNotFoundError",
    "PaaServiceError",
    "PaaTerminalError",
    "PaaTransitionError",
    "PaaValidationError",
    "PositionResolution",
    "PositionSource",
    "ResolvedPosition",
    "approve",
    "demote",
    "list_motions",
    "propose",
    "reject",
    "resolve_actor",
    "resolve_current_position",
    "resolve_position",
    "show",
    "to_position_resolution",
]
