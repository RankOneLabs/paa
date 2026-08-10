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

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
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

#: The contract's names for the cross-field rules this loader enforces.
#:
#: These are not this module's invention. Every string here appears as an
#: ``expected.code`` in the contract's published ``semantic`` case table,
#: and the conformance suite asserts the two sets are equal — so a rule
#: named here that the contract does not recognise fails, and so does a
#: rule the contract names that nothing here emits.
#:
#: Structural rules are deliberately absent. "``version`` must be a
#: positive integer" is the schema's to state, in the schema's own
#: keyword vocabulary; this loader re-checks it defensively because it
#: cannot assume its input was schema-validated, but re-checking a rule
#: does not make it yours to name. Those raise sites carry ``code=None``,
#: which is what lets a conformance case distinguish "rejected by the
#: rule under test" from "rejected on the way there".
PAA_DECLARATION_CODES: frozenset[str] = frozenset({
    "initial_position.not_in_policy",
    "transition.same_position",
    "transition.position_not_in_policy",
    "placement.override_matches_no_evaluator",
    "placement.override_ambiguous",
    "placement.override_duplicate",
    "placement.override_redundant",
})


class PaaDeclarationError(ValueError):
    """A PAA task declaration is missing, malformed, or unresolved.

    Raised for every failure mode this loader guards against: a missing
    declarations directory or file, a duplicate declaration for the same
    task, a malformed or absent access field, an unsupported position or
    deployment value, or an evaluator whose (property, target, technique,
    evaluation_basis, epistemic_status, version, authority) tuple does not
    resolve against the supplied producer registry. Callers must never
    catch this and substitute an invented or permissive default
    declaration.

    ``code`` and ``pointer`` locate the failure in the contract's terms
    rather than in prose: which published rule fired, and the RFC 6901
    pointer to the offending member of the declaration document. Both are
    ``None`` for the structural guards above, whose rules belong to the
    schema.

    They are additive. ``str(exc)`` is unchanged and remains the operator
    -facing message — the CLI prints it and nothing else, so a caller that
    predates this reads exactly what it did before.
    """

    def __init__(
        self, message: str, *, code: str | None = None, pointer: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.pointer = pointer


@dataclass(frozen=True, slots=True)
class PaaEvaluationBasis:
    """The criteria and procedure that ground one evaluator's verdict.

    ``kind`` names the sort of grounding (an invariant set, a reference
    label set, a rubric, a human-gold protocol, a downstream measure) and
    ``ref`` names the concrete one. Both are carried because neither
    identifies a producer alone: two rubric-graded evaluators of the same
    property are different producers when their rubrics differ.
    """

    kind: str
    ref: str


@dataclass(frozen=True, slots=True)
class PaaEvaluator:
    """One declared evaluator, exactly as authored in the YAML declaration.

    ``evaluation_basis`` and ``epistemic_status`` are two axes the contract
    keeps separate: *how* a verdict is grounded, and whether governance
    designates it the task's authoritative truth signal or an
    approximation. An earlier revision of this loader carried a single
    ``oracle`` field conflating them, which could not express the published
    declarations at all — a rubric-graded proxy and a rubric-graded ground
    truth collapsed to the same identity.
    """

    property: str
    target: str
    technique: str
    evaluation_basis: PaaEvaluationBasis
    epistemic_status: str
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
class PaaEvaluatorSelector:
    """Selects exactly one declared evaluator by a subset of its identity.

    ``version`` is optional: omit it when property and technique already
    name one evaluator, supply it to disambiguate when the same property
    is evaluated at two versions. Loading rejects a selector that resolves
    to none or to more than one.
    """

    property: str
    technique: str
    version: str | None

    def matches(self, evaluator: PaaEvaluator) -> bool:
        return (
            evaluator.property == self.property
            and evaluator.technique == self.technique
            and (self.version is None or evaluator.version == self.version)
        )


@dataclass(frozen=True, slots=True)
class PaaPlacementOverride:
    """One evaluator's placement, overriding its position's default."""

    selector: PaaEvaluatorSelector
    placement: PositionPolicyMode


@dataclass(frozen=True, slots=True)
class PaaPlacement:
    """Placement at one declared autonomy position.

    The contract admits two authored forms — a bare mode applying to every
    declared evaluator, or a default plus per-evaluator overrides. Both
    parse to this one shape, so callers never branch on which was written:
    the bare form is a default with no overrides.
    """

    default: PositionPolicyMode
    overrides: tuple[PaaPlacementOverride, ...]

    def for_evaluator(self, evaluator: PaaEvaluator) -> PositionPolicyMode:
        """The mode governing *evaluator* at this position.

        Loading rejects ambiguous and duplicated selectors, so at most one
        override can match and first-match is total rather than arbitrary.
        """
        for override in self.overrides:
            if override.selector.matches(evaluator):
                return override.placement
        return self.default


@dataclass(frozen=True, slots=True)
class PaaPositionPolicy:
    """Placement per *declared* autonomy position.

    The contract admits any non-empty subset of the four positions, so a
    position the declaration omits is absent here rather than defaulted —
    and the cross-field rules enforced at load time require only that
    every position the declaration *references* (initial, and both
    transition endpoints) is one it declares.

    An earlier revision of this loader required all four positions and
    pinned them to a fixed table (manual/autonomous offline, hitl
    blocking, hotl async). The published contract makes placement
    declaration-controlled and refinable per evaluator, which that table
    could not express: a task may hold most evaluators blocking at hotl
    while letting its human gates run async.
    """

    placements: Mapping[str, PaaPlacement]

    def __contains__(self, position: str) -> bool:
        return position in self.placements

    def __getitem__(self, position: str) -> PaaPlacement:
        return self.placements[position]

    @property
    def declared_positions(self) -> tuple[str, ...]:
        return tuple(sorted(self.placements))


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
    evaluation_basis: PaaEvaluationBasis
    epistemic_status: str
    version: str
    authority: str
    status: Literal["implemented", "future"]


_REQUIRED_TASK_KEYS: frozenset[str] = frozenset({
    "task", "version", "deployment", "initial_position", "evaluators",
    "position_policy", "promotion", "demotion",
})
_REQUIRED_PLACEMENT_KEYS: frozenset[str] = frozenset({"default", "overrides"})
_REQUIRED_OVERRIDE_KEYS: frozenset[str] = frozenset({"selector", "placement"})
_REQUIRED_SELECTOR_KEYS: frozenset[str] = frozenset({"property", "technique"})
_REQUIRED_EVALUATOR_KEYS: frozenset[str] = frozenset({
    "property", "target", "technique", "evaluation_basis", "epistemic_status",
    "version", "authority",
})
_REQUIRED_EVALUATION_BASIS_KEYS: frozenset[str] = frozenset({"kind", "ref"})
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
            and registration.evaluation_basis == evaluator.evaluation_basis
            and registration.epistemic_status == evaluator.epistemic_status
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


def _require_mode(raw: object, what: str, path: Path) -> PositionPolicyMode:
    if not isinstance(raw, str) or raw not in _POSITION_POLICY_MODES:
        raise PaaDeclarationError(
            f"{path}: {what} is unsupported: {raw!r} "
            f"(must be one of {sorted(_POSITION_POLICY_MODES)})"
        )
    return raw  # type: ignore[return-value]


def _build_selector(raw: object, what: str, path: Path) -> PaaEvaluatorSelector:
    selector_raw = _require_mapping(raw, what, path)
    _require_keys(selector_raw, _REQUIRED_SELECTOR_KEYS, what, path)

    for key in ("property", "technique"):
        value = selector_raw[key]
        if not isinstance(value, str) or not value:
            raise PaaDeclarationError(f"{path}: {what} {key!r} must be a non-empty string")

    version = selector_raw.get("version")
    if version is not None and (not isinstance(version, str) or not version):
        raise PaaDeclarationError(f"{path}: {what} 'version' must be a non-empty string")

    return PaaEvaluatorSelector(
        property=selector_raw["property"],  # type: ignore[arg-type]
        technique=selector_raw["technique"],  # type: ignore[arg-type]
        version=version,
    )


def _build_placement(raw: object, position: str, path: Path) -> PaaPlacement:
    what = f"position_policy {position!r}"

    # The bare form: one mode for every declared evaluator.
    if isinstance(raw, str):
        return PaaPlacement(default=_require_mode(raw, f"{what} placement", path), overrides=())

    placement_raw = _require_mapping(raw, what, path)
    _require_keys(placement_raw, _REQUIRED_PLACEMENT_KEYS, what, path)

    overrides_raw = placement_raw["overrides"]
    if not isinstance(overrides_raw, list) or not overrides_raw:
        raise PaaDeclarationError(f"{path}: {what} 'overrides' must be a non-empty list")

    overrides: list[PaaPlacementOverride] = []
    for index, override_raw in enumerate(overrides_raw):
        where = f"{what} overrides/{index}"
        override_mapping = _require_mapping(override_raw, where, path)
        _require_keys(override_mapping, _REQUIRED_OVERRIDE_KEYS, where, path)
        overrides.append(
            PaaPlacementOverride(
                selector=_build_selector(override_mapping["selector"], f"{where} selector", path),
                placement=_require_mode(
                    override_mapping["placement"], f"{where} placement", path
                ),
            )
        )

    return PaaPlacement(
        default=_require_mode(placement_raw["default"], f"{what} default", path),
        overrides=tuple(overrides),
    )


def _build_position_policy(raw: object, path: Path) -> PaaPositionPolicy:
    policy_raw = _require_mapping(raw, "position_policy", path)
    if not policy_raw:
        raise PaaDeclarationError(
            f"{path}: 'position_policy' must declare at least one position"
        )

    unknown = sorted(set(policy_raw) - _POSITIONS)
    if unknown:
        raise PaaDeclarationError(
            f"{path}: position_policy declares unsupported position(s) {unknown} "
            f"(must be among {sorted(_POSITIONS)})"
        )

    return PaaPositionPolicy(
        placements=MappingProxyType({
            position: _build_placement(policy_raw[position], position, path)
            for position in sorted(policy_raw)
        })
    )


def _build_evaluation_basis(raw: object, path: Path) -> PaaEvaluationBasis:
    basis_raw = _require_mapping(raw, "evaluation_basis", path)
    _require_keys(basis_raw, _REQUIRED_EVALUATION_BASIS_KEYS, "evaluation_basis", path)

    for key in ("kind", "ref"):
        value = basis_raw[key]
        if not isinstance(value, str) or not value:
            raise PaaDeclarationError(
                f"{path}: evaluation_basis {key!r} must be a non-empty string"
            )

    return PaaEvaluationBasis(
        kind=basis_raw["kind"],  # type: ignore[arg-type]
        ref=basis_raw["ref"],  # type: ignore[arg-type]
    )


def _build_evaluator(
    raw: object, path: Path, registry: Sequence[ProducerRegistration],
) -> PaaEvaluator:
    evaluator_raw = _require_mapping(raw, "evaluator entry", path)
    _require_keys(evaluator_raw, _REQUIRED_EVALUATOR_KEYS, "evaluator entry", path)

    for key in ("property", "target", "technique", "epistemic_status", "authority"):
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
        evaluation_basis=_build_evaluation_basis(evaluator_raw["evaluation_basis"], path),
        epistemic_status=evaluator_raw["epistemic_status"],  # type: ignore[arg-type]
        version=version,
        authority=evaluator_raw["authority"],  # type: ignore[arg-type]
    )
    if _resolve_producer(evaluator, registry) is None:
        basis = evaluator.evaluation_basis
        raise PaaDeclarationError(
            f"{path}: evaluator {evaluator.property!r} "
            f"({evaluator.target}/{evaluator.technique}/"
            f"{basis.kind}:{basis.ref}/{evaluator.epistemic_status} "
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


def _validate_placement_overrides(declaration: PaaTaskDeclaration, path: Path) -> None:
    """Every override names exactly one evaluator, once, and changes something.

    Claims are keyed by the evaluator an override resolves to, never by the
    selector's own text: a versionless selector and one naming that
    evaluator's version are different text and the same evaluator.
    """
    policy = declaration.position_policy
    for position in policy.declared_positions:
        placement = policy[position]
        claimed_by: dict[int, int] = {}

        for index, override in enumerate(placement.overrides):
            where = f"position_policy {position!r} overrides/{index}"
            # Unescaped by construction: _build_position_policy rejects any
            # key outside _POSITIONS, none of which contain '/' or '~', so
            # no position name can reach here needing RFC 6901 escaping.
            at = f"/position_policy/{position}/overrides/{index}"
            matches = [
                i
                for i, evaluator in enumerate(declaration.evaluators)
                if override.selector.matches(evaluator)
            ]
            if not matches:
                raise PaaDeclarationError(
                    f"{path}: {where} selector matches no declared evaluator",
                    code="placement.override_matches_no_evaluator",
                    pointer=f"{at}/selector",
                )
            if len(matches) > 1:
                raise PaaDeclarationError(
                    f"{path}: {where} selector matches {len(matches)} declared "
                    "evaluators; add 'version' to disambiguate",
                    code="placement.override_ambiguous",
                    pointer=f"{at}/selector",
                )

            claimant = claimed_by.setdefault(matches[0], index)
            if claimant != index:
                raise PaaDeclarationError(
                    f"{path}: {where} selector selects the evaluator already "
                    f"overridden at {position} by overrides/{claimant}",
                    code="placement.override_duplicate",
                    pointer=f"{at}/selector",
                )

            if override.placement == placement.default:
                raise PaaDeclarationError(
                    f"{path}: {where} placement {override.placement!r} equals the "
                    f"{position} default; remove the override or change its placement",
                    code="placement.override_redundant",
                    pointer=f"{at}/placement",
                )


def _validate_semantics(declaration: PaaTaskDeclaration, path: Path) -> None:
    """The contract's cross-field rules, which no single field can carry.

    Separate from the per-field builders because each rule spans two parts
    of the declaration — a position and the policy declaring it, a selector
    and the evaluator list it resolves against — so none of them can be
    checked while the pieces are still being built.
    """
    policy = declaration.position_policy
    declared = list(policy.declared_positions)

    if declaration.initial_position not in policy:
        raise PaaDeclarationError(
            f"{path}: initial_position {declaration.initial_position!r} is not "
            f"declared in position_policy (declared: {declared})",
            code="initial_position.not_in_policy",
            pointer="/initial_position",
        )

    transitions = (
        ("promotion", declaration.promotion.from_position, declaration.promotion.to_position),
        ("demotion", declaration.demotion.from_position, declaration.demotion.to_position),
    )
    for name, from_position, to_position in transitions:
        if from_position == to_position:
            raise PaaDeclarationError(
                f"{path}: {name}.from and {name}.to are both {from_position!r}",
                code="transition.same_position",
                # The transition, not either edge: neither one is wrong on
                # its own, and the contract points at the pair.
                pointer=f"/{name}",
            )
        for edge, position in (("from", from_position), ("to", to_position)):
            if position not in policy:
                raise PaaDeclarationError(
                    f"{path}: {name}.{edge} {position!r} is not declared in "
                    f"position_policy (declared: {declared})",
                    code="transition.position_not_in_policy",
                    # The document's key, not the dataclass field: these
                    # are `from`/`to` on the wire and `from_position`/
                    # `to_position` only after loading.
                    pointer=f"/{name}/{edge}",
                )

    _validate_placement_overrides(declaration, path)


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

    declaration = PaaTaskDeclaration(
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
    _validate_semantics(declaration, path)
    return declaration


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
    "PAA_DECLARATION_CODES",
    "AutonomyPosition",
    "Deployment",
    "PaaDeclarationError",
    "PaaDemotion",
    "PaaEvaluationBasis",
    "PaaEvaluator",
    "PaaEvaluatorSelector",
    "PaaPlacement",
    "PaaPlacementOverride",
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
