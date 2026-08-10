"""What each published declaration is pinned to, and the check for it.

These are not contract rules. Nothing in ``paa-task.schema.json`` says a
declaration named ``outbound_content_publish.v1.yaml`` must be deployment
``active`` with exactly four evaluators — that is a fact about *this*
fixture, asserted so the corpus cannot drift underneath the documentation
and diagrams built on it. Every implementation would be right to ignore
it; the corpus would not survive without it.

Ported from ``pinnedInvariants`` in the site's ``task-conformance.mjs``,
which this suite is on course to replace. Faithfully rather than freshly:
the point of the ratchet is that the same fifteen cases keep failing for
the same fifteen reasons after the JS goes away, and a table rewritten
from the fixtures would agree with whatever the fixtures currently say.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from conformance._corpus import SchemaViolation


@dataclass(frozen=True, slots=True)
class EvaluatorRequirement:
    """One evaluator a declaration must carry, and what it must say.

    ``identity`` is the subset of fields used to *find* the evaluator and
    ``expected`` the full set its fields are then checked against. They
    are separate because the two failures differ: an evaluator whose
    identity does not resolve is missing (the declaration lost it), while
    one that resolves with a wrong field has drifted. A single map would
    report every drift as a disappearance.
    """

    label: str
    identity: Mapping[str, str]
    expected: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class PinnedRequirements:
    deployment: str
    evaluator_count: int
    evaluators: tuple[EvaluatorRequirement, ...]
    #: ``None`` means "must declare no scopes", distinct from a tuple of
    #: the exact scopes required. An empty tuple would conflate them.
    scopes: tuple[str, ...] | None = None
    forbidden_evaluator_properties: tuple[str, ...] = field(default_factory=tuple)


def _evaluator(
    label: str, identity: Mapping[str, str], **expected: str,
) -> EvaluatorRequirement:
    return EvaluatorRequirement(label=label, identity=identity, expected=expected)


PINNED_REQUIREMENTS: Mapping[str, PinnedRequirements] = {
    "outbound_content_publish.v1.yaml": PinnedRequirements(
        deployment="active",
        evaluator_count=4,
        forbidden_evaluator_properties=("author_rate",),
        scopes=("publish:bluesky", "publish:farcaster"),
        evaluators=(
            _evaluator(
                "outbound_content_invariants",
                {"property": "outbound_content_invariants"},
                property="outbound_content_invariants", target="output",
                technique="deterministic", epistemic_status="ground_truth",
                version="1", authority="blocking",
            ),
            _evaluator(
                "draft_quality",
                {"property": "draft_quality"},
                property="draft_quality", target="output",
                technique="llm_judge", epistemic_status="proxy",
                version="1", authority="advisory",
            ),
            _evaluator(
                "publish_authorization",
                {"property": "publish_authorization"},
                property="publish_authorization", target="process",
                technique="human", epistemic_status="ground_truth",
                version="1", authority="blocking",
            ),
            _evaluator(
                "publish_quality",
                {"property": "publish_quality"},
                property="publish_quality", target="outcome",
                technique="human", epistemic_status="ground_truth",
                version="1", authority="advisory",
            ),
        ),
    ),
    "inbound_reply_surfacing.v1.yaml": PinnedRequirements(
        deployment="shadow",
        evaluator_count=4,
        scopes=None,
        evaluators=(
            _evaluator(
                "content_invariants",
                {"property": "content_invariants"},
                property="content_invariants", target="output",
                technique="deterministic", epistemic_status="ground_truth",
                version="1", authority="blocking",
            ),
            _evaluator(
                "author_rate",
                {"property": "author_rate"},
                property="author_rate", target="process",
                technique="deterministic", epistemic_status="ground_truth",
                version="1", authority="blocking",
            ),
            # The corpus's one succession pair: same property, two live
            # evaluators. Identity has to name technique and version or
            # the two requirements would both resolve to whichever comes
            # first, and one of them would never be checked.
            _evaluator(
                "response_quality llm_judge v1",
                {"property": "response_quality", "technique": "llm_judge", "version": "1"},
                property="response_quality", target="output",
                technique="llm_judge", epistemic_status="proxy",
                version="1", authority="advisory",
            ),
            _evaluator(
                "response_quality human v2",
                {"property": "response_quality", "technique": "human", "version": "2"},
                property="response_quality", target="output",
                technique="human", epistemic_status="ground_truth",
                version="2", authority="advisory",
            ),
        ),
    ),
    "canonical_promotion.v1.yaml": PinnedRequirements(
        deployment="disabled",
        evaluator_count=2,
        scopes=None,
        evaluators=(
            _evaluator(
                "claim_admissibility",
                {"property": "claim_admissibility"},
                property="claim_admissibility", target="input",
                technique="deterministic", epistemic_status="ground_truth",
                version="1", authority="blocking",
            ),
            _evaluator(
                "canonical_truth",
                {"property": "canonical_truth"},
                property="canonical_truth", target="output",
                technique="human", epistemic_status="ground_truth",
                version="1", authority="blocking",
            ),
        ),
    ),
}


def _find(evaluators: Sequence[Any], identity: Mapping[str, str]) -> int:
    for index, evaluator in enumerate(evaluators):
        if isinstance(evaluator, Mapping) and all(
            evaluator.get(field_name) == value for field_name, value in identity.items()
        ):
            return index
    return -1


def _evaluator_violations(
    evaluators: Sequence[Any], requirement: EvaluatorRequirement,
) -> list[SchemaViolation]:
    index = _find(evaluators, requirement.identity)
    if index < 0:
        return [SchemaViolation(code="pinned.evaluator_missing", pointer="/evaluators")]

    evaluator = evaluators[index]
    return [
        SchemaViolation(
            code="pinned.evaluator_field_mismatch",
            pointer=f"/evaluators/{index}/{field_name}",
        )
        for field_name, expected in requirement.expected.items()
        if evaluator.get(field_name) != expected
    ]


def pinned_violations(source_name: str, declaration: Any) -> tuple[SchemaViolation, ...]:
    """Every pinned invariant *declaration* breaks, as codes and pointers.

    A fixture with no pinned requirements yields nothing — that is the
    right answer, not a gap: ``refund_approval`` is deliberately
    unpinned, so it stays free to be edited as the contract's worked
    example.
    """
    requirements = PINNED_REQUIREMENTS.get(source_name)
    if requirements is None:
        return ()

    evaluators = declaration.get("evaluators") or []
    found: list[SchemaViolation] = []

    if declaration.get("deployment") != requirements.deployment:
        found.append(SchemaViolation("pinned.deployment_mismatch", "/deployment"))

    if len(evaluators) != requirements.evaluator_count:
        found.append(SchemaViolation("pinned.evaluator_count_mismatch", "/evaluators"))

    for forbidden in requirements.forbidden_evaluator_properties:
        index = _find(evaluators, {"property": forbidden})
        if index >= 0:
            found.append(
                SchemaViolation("pinned.forbidden_evaluator", f"/evaluators/{index}/property")
            )

    declared_scopes = declaration.get("scopes") or []
    if requirements.scopes is None:
        if declared_scopes:
            found.append(SchemaViolation("pinned.scopes_forbidden", "/scopes"))
    elif sorted(declared_scopes) != sorted(requirements.scopes):
        found.append(SchemaViolation("pinned.scopes_mismatch", "/scopes"))

    for requirement in requirements.evaluators:
        found.extend(_evaluator_violations(evaluators, requirement))

    promotion = declaration.get("promotion")
    execution = promotion.get("execution") if isinstance(promotion, Mapping) else None
    if execution != "operator_approval":
        found.append(
            SchemaViolation("pinned.promotion_execution_mismatch", "/promotion/execution")
        )

    return tuple(found)
