"""A thin, fail-closed accessor over a consumer's PAA task declarations.

The normative Progressive Autonomy Architecture task-declaration contract
(schema, vocabulary, and cross-field semantics) is owned by paa.dev and
published as ``paa-task.schema.json``. This module does not re-implement
or encode that external schema — schema conformance is asserted by the
conformance suite against the published contract artifacts, not here. It
is a narrow loader over already-conformant YAML declarations in a
consumer-supplied directory, turning them into small immutable
dataclasses callers can import instead of re-parsing YAML themselves.

The producer registry is *not* owned here. Which evaluator identities
exist, and what code produces each verdict, is consumer domain data —
evaluator succession is the consumer's governed concern. This module owns
only the registry's *shape* (``ProducerRegistration``) and the rule
enforced against it: every evaluator any loaded declaration references
must resolve against the registry the caller supplies, or loading fails
closed. There is no module-global registry and no silent fallback to an
unregistered producer.

Nothing here resolves a path or a registry at import time. ``directory``
and ``registry`` are required arguments on both public entry points — see
``paa_runtime.config.RuntimeConfig`` for the consolidated construction
surface.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

AutonomyPosition = Literal["manual", "hitl", "hotl", "autonomous"]
Deployment = Literal["active", "shadow", "disabled"]
WindowKind = Literal["cases", "duration"]
PromotionExecution = Literal["operator_approval", "automatic"]
PositionPolicyMode = Literal["offline", "blocking", "async"]

# Every membership test against these sets is guarded by an isinstance(str)
# check first. YAML admits mappings and sequences anywhere a scalar is
# expected, and those are unhashable — `{"a": 1} in frozenset(...)` raises
# TypeError, not False. Without the guard a malformed declaration escapes
# this module as TypeError instead of PaaDeclarationError, which is the one
# thing the loader promises never to do.
_POSITIONS: frozenset[str] = frozenset({"manual", "hitl", "hotl", "autonomous"})
_DEPLOYMENTS: frozenset[str] = frozenset({"active", "shadow", "disabled"})
_WINDOW_KINDS: frozenset[str] = frozenset({"cases", "duration"})
_PROMOTION_EXECUTIONS: frozenset[str] = frozenset({"operator_approval", "automatic"})
_POSITION_POLICY_MODES: frozenset[str] = frozenset({"offline", "blocking", "async"})

# The one runtime vocabulary every checked-in declaration's position_policy
# must declare, exactly. manual/autonomous are offline (no governed effect
# ever runs), hitl is blocking (synchronous human authorization required
# before the effect), hotl is async (the effect runs, review happens after).
# Declaration loading fails closed if a declaration maps a supported
# position differently — runtime semantics and the validated declaration
# must never silently diverge.
EXPECTED_POSITION_POLICY: dict[str, str] = {
    "manual": "offline",
    "hitl": "blocking",
    "hotl": "async",
    "autonomous": "offline",
}


class PaaDeclarationError(ValueError):
    """A PAA task declaration is missing, malformed, or unresolved.

    Raised for every failure mode this loader guards against: a missing
    declarations directory or file, a duplicate declaration for the same
    task, a malformed or absent access field, an unsupported position or
    deployment value, or an evaluator whose (property, target, technique,
    oracle, version, authority) tuple does not resolve against
    the supplied producer registry. Callers must never catch this and
    substitute an
    invented or permissive default declaration.
    """


@dataclass(frozen=True, slots=True)
class PaaEvaluator:
    """One declared evaluator, exactly as authored in the YAML declaration."""

    property: str
    target: str
    technique: str
    oracle: str
    version: str
    authority: str


@dataclass(frozen=True, slots=True)
class PaaWindow:
    """An evidence window: a case count or an ISO-8601 duration string."""

    kind: WindowKind
    size: int | str


@dataclass(frozen=True, slots=True)
class PaaPromotion:
    """The one promotion transition a declaration defines."""

    from_position: AutonomyPosition
    to_position: AutonomyPosition
    report: str
    window: PaaWindow
    execution: PromotionExecution


@dataclass(frozen=True, slots=True)
class PaaDemotion:
    """The one demotion transition a declaration defines."""

    from_position: AutonomyPosition
    to_position: AutonomyPosition
    trigger: str
    window: PaaWindow


@dataclass(frozen=True, slots=True)
class PaaPositionPolicy:
    """Runtime semantics per autonomy position, exactly as declared.

    Validated at load time against ``EXPECTED_POSITION_POLICY`` — the
    checked-in vocabulary (manual/autonomous offline, hitl blocking, hotl
    async) is fixed, not merely a convention any declaration may redefine.
    """

    manual: PositionPolicyMode
    hitl: PositionPolicyMode
    hotl: PositionPolicyMode
    autonomous: PositionPolicyMode


@dataclass(frozen=True, slots=True)
class PaaTaskDeclaration:
    """One task declaration's access fields, as authored in its YAML."""

    task: str
    version: int
    deployment: Deployment
    initial_position: AutonomyPosition
    evaluators: tuple[PaaEvaluator, ...]
    position_policy: PaaPositionPolicy
    promotion: PaaPromotion
    demotion: PaaDemotion
    scopes: tuple[str, ...] | None


@dataclass(frozen=True, slots=True)
class ProducerRegistration:
    """One registry entry mapping an evaluator identity to its producer.

    ``status`` is "implemented" when ``version`` is sourced from a live
    producer-version constant, or "future" for an evaluator a checked-in
    declaration already references but whose producer has not yet been
    built — reserved here so declaration loading doesn't fail
    closed on identities the final spec already named.
    """

    property: str
    target: str
    technique: str
    oracle: str
    version: str
    authority: str
    status: Literal["implemented", "future"]


_REQUIRED_TASK_KEYS: frozenset[str] = frozenset({
    "task", "version", "deployment", "initial_position", "evaluators",
    "position_policy", "promotion", "demotion",
})
_REQUIRED_POSITION_POLICY_KEYS: frozenset[str] = frozenset({
    "manual", "hitl", "hotl", "autonomous",
})
_REQUIRED_EVALUATOR_KEYS: frozenset[str] = frozenset({
    "property", "target", "technique", "oracle", "version", "authority",
})
_REQUIRED_PROMOTION_KEYS: frozenset[str] = frozenset({
    "from", "to", "report", "window", "execution",
})
_REQUIRED_DEMOTION_KEYS: frozenset[str] = frozenset({
    "from", "to", "trigger", "window",
})


def _resolve_producer(
    evaluator: PaaEvaluator, registry: Sequence[ProducerRegistration],
) -> ProducerRegistration | None:
    for registration in registry:
        if (
            registration.property == evaluator.property
            and registration.target == evaluator.target
            and registration.technique == evaluator.technique
            and registration.oracle == evaluator.oracle
            and registration.version == evaluator.version
            and registration.authority == evaluator.authority
        ):
            return registration
    return None


def _require_mapping(raw: object, what: str, path: Path) -> dict[str, object]:
    if not isinstance(raw, dict):
        raise PaaDeclarationError(f"{path}: {what} must be a mapping")
    return raw


def _require_keys(raw: dict[str, object], required: frozenset[str], what: str, path: Path) -> None:
    missing = required - raw.keys()
    if missing:
        raise PaaDeclarationError(
            f"{path}: {what} missing required field(s): {sorted(missing)}"
        )


def _require_position(raw: dict[str, object], key: str, what: str, path: Path) -> AutonomyPosition:
    value = raw[key]
    if not isinstance(value, str) or value not in _POSITIONS:
        raise PaaDeclarationError(
            f"{path}: {what} {key!r} is unsupported: {value!r} "
            f"(must be one of {sorted(_POSITIONS)})"
        )
    return value  # type: ignore[return-value]


def _build_window(raw: object, path: Path) -> PaaWindow:
    window = _require_mapping(raw, "window", path)
    _require_keys(window, frozenset({"kind", "size"}), "window", path)
    kind = window["kind"]
    size = window["size"]
    if not isinstance(kind, str) or kind not in _WINDOW_KINDS:
        raise PaaDeclarationError(
            f"{path}: unsupported window kind {kind!r} (must be one of {sorted(_WINDOW_KINDS)})"
        )
    if kind == "cases":
        if not isinstance(size, int) or isinstance(size, bool) or size < 1:
            raise PaaDeclarationError(f"{path}: cases window 'size' must be a positive integer")
    elif not isinstance(size, str) or not size:
        raise PaaDeclarationError(f"{path}: duration window 'size' must be a non-empty string")
    return PaaWindow(kind=kind, size=size)  # type: ignore[arg-type]


def _build_promotion(raw: object, path: Path) -> PaaPromotion:
    promotion = _require_mapping(raw, "promotion", path)
    _require_keys(promotion, _REQUIRED_PROMOTION_KEYS, "promotion", path)
    execution = promotion["execution"]
    if not isinstance(execution, str) or execution not in _PROMOTION_EXECUTIONS:
        raise PaaDeclarationError(
            f"{path}: unsupported promotion execution {execution!r} "
            f"(must be one of {sorted(_PROMOTION_EXECUTIONS)})"
        )
    report = promotion["report"]
    if not isinstance(report, str) or not report:
        raise PaaDeclarationError(f"{path}: promotion 'report' must be a non-empty string")
    return PaaPromotion(
        from_position=_require_position(promotion, "from", "promotion", path),
        to_position=_require_position(promotion, "to", "promotion", path),
        report=report,
        window=_build_window(promotion["window"], path),
        execution=execution,  # type: ignore[arg-type]
    )


def _build_demotion(raw: object, path: Path) -> PaaDemotion:
    demotion = _require_mapping(raw, "demotion", path)
    _require_keys(demotion, _REQUIRED_DEMOTION_KEYS, "demotion", path)
    trigger = demotion["trigger"]
    if not isinstance(trigger, str) or not trigger:
        raise PaaDeclarationError(f"{path}: demotion 'trigger' must be a non-empty string")
    return PaaDemotion(
        from_position=_require_position(demotion, "from", "demotion", path),
        to_position=_require_position(demotion, "to", "demotion", path),
        trigger=trigger,
        window=_build_window(demotion["window"], path),
    )


def _build_position_policy(raw: object, path: Path) -> PaaPositionPolicy:
    policy_raw = _require_mapping(raw, "position_policy", path)
    _require_keys(policy_raw, _REQUIRED_POSITION_POLICY_KEYS, "position_policy", path)

    resolved: dict[str, str] = {}
    for position in sorted(_REQUIRED_POSITION_POLICY_KEYS):
        mode = policy_raw[position]
        if not isinstance(mode, str) or mode not in _POSITION_POLICY_MODES:
            raise PaaDeclarationError(
                f"{path}: position_policy {position!r} is unsupported: {mode!r} "
                f"(must be one of {sorted(_POSITION_POLICY_MODES)})"
            )
        resolved[position] = mode

    if resolved != EXPECTED_POSITION_POLICY:
        raise PaaDeclarationError(
            f"{path}: position_policy {resolved} does not match the fixed "
            f"runtime vocabulary {EXPECTED_POSITION_POLICY}"
        )
    return PaaPositionPolicy(
        manual=resolved["manual"],  # type: ignore[arg-type]
        hitl=resolved["hitl"],  # type: ignore[arg-type]
        hotl=resolved["hotl"],  # type: ignore[arg-type]
        autonomous=resolved["autonomous"],  # type: ignore[arg-type]
    )


def _build_evaluator(
    raw: object, path: Path, registry: Sequence[ProducerRegistration],
) -> PaaEvaluator:
    evaluator_raw = _require_mapping(raw, "evaluator entry", path)
    _require_keys(evaluator_raw, _REQUIRED_EVALUATOR_KEYS, "evaluator entry", path)

    for key in ("property", "target", "technique", "oracle", "authority"):
        value = evaluator_raw[key]
        if not isinstance(value, str) or not value:
            raise PaaDeclarationError(f"{path}: evaluator {key!r} must be a non-empty string")

    version = evaluator_raw["version"]
    if not isinstance(version, str) or not version:
        raise PaaDeclarationError(f"{path}: evaluator 'version' must be a non-empty string")

    evaluator = PaaEvaluator(
        property=evaluator_raw["property"],  # type: ignore[arg-type]
        target=evaluator_raw["target"],  # type: ignore[arg-type]
        technique=evaluator_raw["technique"],  # type: ignore[arg-type]
        oracle=evaluator_raw["oracle"],  # type: ignore[arg-type]
        version=version,
        authority=evaluator_raw["authority"],  # type: ignore[arg-type]
    )
    if _resolve_producer(evaluator, registry) is None:
        raise PaaDeclarationError(
            f"{path}: evaluator {evaluator.property!r} "
            f"({evaluator.target}/{evaluator.technique}/{evaluator.oracle} "
            f"v{evaluator.version}, {evaluator.authority}) does not resolve "
            "against the supplied producer registry"
        )
    return evaluator


def _build_scopes(task_raw: dict[str, object], path: Path) -> tuple[str, ...] | None:
    """The declared runtime scopes, or None when the key is absent.

    Takes the whole declaration mapping rather than the value so an absent
    ``scopes`` key stays distinguishable from a present-but-null one. YAML
    parses a bare ``scopes:`` to None, and the published schema admits
    ``scopes`` only as an array — so an explicit null is a malformed
    declaration and must fail closed, not quietly read as "this task
    declares no scopes".
    """
    if "scopes" not in task_raw:
        return None
    raw = task_raw["scopes"]
    if not isinstance(raw, list) or not raw:
        raise PaaDeclarationError(f"{path}: 'scopes' must be a non-empty list")
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str) or not item:
            raise PaaDeclarationError(f"{path}: 'scopes' entries must be non-empty strings")
        if item in seen:
            raise PaaDeclarationError(f"{path}: 'scopes' must not contain duplicates: {item!r}")
        seen.add(item)
    return tuple(raw)


def _build_declaration(
    raw: object, path: Path, registry: Sequence[ProducerRegistration],
) -> PaaTaskDeclaration:
    task_raw = _require_mapping(raw, "declaration", path)
    _require_keys(task_raw, _REQUIRED_TASK_KEYS, "declaration", path)

    task = task_raw["task"]
    if not isinstance(task, str) or not task:
        raise PaaDeclarationError(f"{path}: 'task' must be a non-empty string")

    version = task_raw["version"]
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise PaaDeclarationError(f"{path}: 'version' must be a positive integer")

    deployment = task_raw["deployment"]
    if not isinstance(deployment, str) or deployment not in _DEPLOYMENTS:
        raise PaaDeclarationError(
            f"{path}: unsupported deployment {deployment!r} "
            f"(must be one of {sorted(_DEPLOYMENTS)})"
        )

    evaluators_raw = task_raw["evaluators"]
    if not isinstance(evaluators_raw, list) or not evaluators_raw:
        raise PaaDeclarationError(f"{path}: 'evaluators' must be a non-empty list")

    return PaaTaskDeclaration(
        task=task,
        version=version,
        deployment=deployment,  # type: ignore[arg-type]
        initial_position=_require_position(task_raw, "initial_position", "declaration", path),
        evaluators=tuple(_build_evaluator(e, path, registry) for e in evaluators_raw),
        position_policy=_build_position_policy(task_raw["position_policy"], path),
        promotion=_build_promotion(task_raw["promotion"], path),
        demotion=_build_demotion(task_raw["demotion"], path),
        scopes=_build_scopes(task_raw, path),
    )


def load_paa_declarations(
    directory: Path | str, *, registry: Sequence[ProducerRegistration],
) -> dict[str, PaaTaskDeclaration]:
    """Load every checked-in PAA task declaration in *directory*.

    Fails closed (raising PaaDeclarationError) on a missing directory, an
    empty directory, a file that fails to parse as YAML, a malformed or
    missing access field, an unsupported position/deployment/execution
    value, an evaluator that doesn't resolve against the supplied
    registry, or
    a second file declaring a task already seen. Returns a mapping of
    task name to its one declaration.
    """
    dir_path = Path(directory)
    if not dir_path.is_dir():
        raise PaaDeclarationError(f"PAA declarations directory not found: {dir_path}")

    paths = sorted(dir_path.glob("*.yaml"))
    if not paths:
        raise PaaDeclarationError(f"no PAA task declarations found in {dir_path}")

    declarations: dict[str, PaaTaskDeclaration] = {}
    for path in paths:
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise PaaDeclarationError(f"{path}: invalid YAML: {exc}") from exc

        declaration = _build_declaration(raw, path, registry)
        existing = declarations.get(declaration.task)
        if existing is not None:
            raise PaaDeclarationError(
                f"{path}: duplicate PAA declaration for task {declaration.task!r} "
                f"(already loaded at version {existing.version})"
            )
        declarations[declaration.task] = declaration

    return declarations


def get_paa_declaration(
    task: str, *, directory: Path | str, registry: Sequence[ProducerRegistration],
) -> PaaTaskDeclaration:
    """Return the one checked-in declaration for *task*, requiring exact identity.

    Raises PaaDeclarationError for an unknown task rather than returning
    None or an invented default — callers must handle absence explicitly.
    """
    declarations = load_paa_declarations(directory, registry=registry)
    try:
        return declarations[task]
    except KeyError:
        raise PaaDeclarationError(
            f"no PAA declaration for task {task!r} in {directory}"
        ) from None


__all__ = [
    "EXPECTED_POSITION_POLICY",
    "AutonomyPosition",
    "Deployment",
    "PaaDeclarationError",
    "PaaDemotion",
    "PaaEvaluator",
    "PaaPositionPolicy",
    "PaaPromotion",
    "PaaTaskDeclaration",
    "PaaWindow",
    "PositionPolicyMode",
    "PromotionExecution",
    "ProducerRegistration",
    "WindowKind",
    "get_paa_declaration",
    "load_paa_declarations",
]
