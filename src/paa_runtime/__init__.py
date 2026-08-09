"""paa-runtime: the executable side of the Progressive Autonomy Architecture.

Adopting this package is two things: build one ``RuntimeConfig``, then call
the lifecycle API. Nothing else is required — the runtime owns its own
event store, so no consumer has to host a table or implement a protocol.

    from pathlib import Path
    from paa_runtime import RuntimeConfig, SqliteEventStore, propose, approve

    config = RuntimeConfig(
        declarations_dir=Path("contracts/paa"),
        evidence_root=Path("."),
        registry=MY_PRODUCER_REGISTRY,
        db_path=Path("paa_runtime.db"),
        actor_env_var="MY_APP_PAA_ACTOR",
    )
    store = SqliteEventStore(config.db_path)

    motion = propose(
        store, config, task="refund_approval", scope=None,
        to_position="hotl", evidence_path=Path("report.json"),
    )
    approve(store, config, motion_id=motion.motion_id, reason="window closed eligible")

What this package governs: task declarations, the motion lifecycle
(propose / approve / reject / demote), position resolution, evidence
binding, and the append-only event store. What it does not: producing
evaluator verdicts, or evaluating promotion rules — thresholds are
declared, not machine-evaluated, and approval is an operator judgment.
The README's "What it does not do" section states the non-matches
against the paa.dev contracts in full.
"""

from __future__ import annotations

from paa_runtime.config import RuntimeConfig
from paa_runtime.declarations import (
    EXPECTED_POSITION_POLICY,
    AutonomyPosition,
    Deployment,
    PaaDeclarationError,
    PaaDemotion,
    PaaEvaluator,
    PaaPositionPolicy,
    PaaPromotion,
    PaaTaskDeclaration,
    PaaWindow,
    PositionPolicyMode,
    ProducerRegistration,
    PromotionExecution,
    WindowKind,
    get_paa_declaration,
    load_paa_declarations,
)
from paa_runtime.events import (
    AUTONOMY_EVENT_TYPES,
    AUTONOMY_POSITIONS,
    CURRENT_EVENT_SCHEMA,
    MOTION_STATUSES,
    AutonomyEventType,
    MotionStatus,
    new_created_at,
    new_event_id,
    new_motion_id,
)
from paa_runtime.evidence import (
    EvidenceError,
    compute_sha256,
    evidence_ref_for,
    read_evidence_bytes,
    store_evidence,
    verify_evidence,
)
from paa_runtime.service import (
    Motion,
    PaaCorruptHistoryError,
    PaaNotFoundError,
    PaaServiceError,
    PaaTerminalError,
    PaaTransitionError,
    PaaValidationError,
    PositionResolution,
    PositionSource,
    ResolvedPosition,
    approve,
    demote,
    list_motions,
    propose,
    reject,
    resolve_actor,
    resolve_current_position,
    resolve_position,
    show,
    to_position_resolution,
)
from paa_runtime.sqlite_store import SqliteEventStore
from paa_runtime.store import AutonomyEvent, EventStore

__version__ = "0.2.0"

__all__ = [
    # Construction surface
    "RuntimeConfig",
    "SqliteEventStore",
    "EventStore",
    # Lifecycle API
    "propose",
    "approve",
    "reject",
    "demote",
    "show",
    "list_motions",
    "resolve_current_position",
    "resolve_position",
    "to_position_resolution",
    "resolve_actor",
    # Projections and rows
    "Motion",
    "AutonomyEvent",
    "ResolvedPosition",
    "PositionResolution",
    "PositionSource",
    # Declarations
    "PaaTaskDeclaration",
    "PaaEvaluator",
    "PaaWindow",
    "PaaPromotion",
    "PaaDemotion",
    "PaaPositionPolicy",
    "ProducerRegistration",
    "load_paa_declarations",
    "get_paa_declaration",
    "EXPECTED_POSITION_POLICY",
    # Evidence
    "store_evidence",
    "verify_evidence",
    "read_evidence_bytes",
    "compute_sha256",
    "evidence_ref_for",
    # Vocabulary
    "AutonomyPosition",
    "AutonomyEventType",
    "MotionStatus",
    "Deployment",
    "WindowKind",
    "PromotionExecution",
    "PositionPolicyMode",
    "AUTONOMY_POSITIONS",
    "AUTONOMY_EVENT_TYPES",
    "MOTION_STATUSES",
    "CURRENT_EVENT_SCHEMA",
    "new_motion_id",
    "new_event_id",
    "new_created_at",
    # Errors
    "PaaDeclarationError",
    "EvidenceError",
    "PaaServiceError",
    "PaaNotFoundError",
    "PaaValidationError",
    "PaaTransitionError",
    "PaaCorruptHistoryError",
    "PaaTerminalError",
    "__version__",
]
