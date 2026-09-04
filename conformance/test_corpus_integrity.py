"""Is the published corpus self-consistent — and is each negative case
actually invalid, for the reason it claims?

A different question from the rest of this directory. The other suites
ask whether ``paa_runtime`` honours the contract, and their answers would
change if the implementation changed. Nothing here imports
``paa_runtime`` at all: every assertion is about the artifacts, and would
hold identically for an implementation in any language, or none.

That is why it can claim stages the conformance suites decline. A case
like ``decision_undeclared_scope`` has no entry point in a runtime that
only governs motions it writes itself — but "does this document violate
the contract?" is answerable without one, and answering it is what keeps
a fixture edit from quietly turning a negative case into a valid
document that no longer tests anything.

Coverage is deliberately partial and the counts below say exactly how
far it goes. Structural and pinned stages are claimed here; the runtime
-artifact ``*_semantic`` stages are not yet, and the JS validator that
covers them stays until they are.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import paa_contracts as contracts
import pytest

from conformance._corpus import (
    SCHEMA_FOR_KIND,
    SchemaViolation,
    case_documents,
    expected_violation,
    load_document,
    violations,
)
from conformance._pinned import PINNED_REQUIREMENTS, pinned_violations

STRUCTURAL_CASES = [
    (kind, case)
    for kind in SCHEMA_FOR_KIND
    for case in contracts.invalid_cases(kind, stage="structural")
]

PINNED_CASES = list(contracts.invalid_cases("task", stage="pinned"))

ALL_CASES = [
    (kind, case) for kind in SCHEMA_FOR_KIND for case in contracts.invalid_cases(kind)
]

PUBLISHED_FIXTURES = [
    *(("operating", p) for p in contracts.operating_record_paths()),
    *(("task", p) for p in contracts.task_declaration_paths()),
    *(("event", p) for p in contracts.autonomy_event_paths()),
    *(("evidence", p) for p in contracts.evidence_record_paths()),
    *(("decision", p) for p in contracts.decision_artifact_paths()),
]


def _case_id(value: Any) -> str:
    return value["id"] if isinstance(value, dict) else str(value)


class TestTheCorpusIsWhatItClaims:
    """Counts, pinned so a silently shrinking corpus is loud.

    Every parametrised class below degenerates to zero assertions if its
    case list comes back empty, and zero assertions pass. These are the
    guard against that.
    """

    def test_the_case_table_count_is_pinned(self) -> None:
        assert len(ALL_CASES) == 147

    def test_the_structural_case_count_is_pinned(self) -> None:
        assert len(STRUCTURAL_CASES) == 102

    def test_fifteen_of_them_are_pinned(self) -> None:
        assert len(PINNED_CASES) == 15

    def test_every_published_fixture_is_discoverable(self) -> None:
        assert len(PUBLISHED_FIXTURES) == 22


class TestFormatAssertionIsLive:
    """``format`` must actually be checked, not merely requested.

    JSON Schema treats ``format`` as an annotation by default, and
    ``jsonschema`` asserts it only when the per-format validators are
    installed. Without them a ``FormatChecker`` is accepted, checks
    nothing, and the three cases that mutate a date-time into
    ``"not-a-date"`` pass validation — a silent hole that looks exactly
    like coverage. Asserted here so the dependency cannot be dropped
    without a named failure.
    """

    def test_a_malformed_date_time_is_rejected(self) -> None:
        found = violations("decision", {"generated_at": "not-a-date"})
        assert SchemaViolation("format", "/generated_at") in found


class TestThePositiveCorpusIsValid:
    """Every published fixture validates. Everything else rests on this."""

    @pytest.mark.parametrize(("kind", "path"), PUBLISHED_FIXTURES, ids=lambda v: Path(str(v)).name)
    def test_published_fixture_has_no_schema_violations(self, kind: str, path: Path) -> None:
        assert violations(kind, load_document(path)) == ()

    @pytest.mark.parametrize(("kind", "case"), ALL_CASES, ids=_case_id)
    def test_the_unmutated_base_of_every_case_is_valid(
        self, kind: str, case: contracts.InvalidCase,
    ) -> None:
        # The premise every negative assertion depends on. A case whose
        # base already violated the schema would satisfy its own
        # expectation while proving nothing about its mutation, and this
        # is the only place that distinction is visible.
        base, _ = case_documents(kind, case)
        assert violations(kind, base) == ()


class TestStructuralCases:
    """All fifty, by exact keyword and pointer."""

    @pytest.mark.parametrize(("kind", "case"), STRUCTURAL_CASES, ids=_case_id)
    def test_structural_case_violates_the_expected_keyword(
        self, kind: str, case: contracts.InvalidCase,
    ) -> None:
        _, mutated = case_documents(kind, case)
        assert expected_violation(case) in violations(kind, mutated)


class TestPinnedCases:
    """All fifteen, and the published declarations they are pinned to."""

    @pytest.mark.parametrize("case", PINNED_CASES, ids=_case_id)
    def test_pinned_case_breaks_the_expected_invariant(
        self, case: contracts.InvalidCase,
    ) -> None:
        _, mutated = case_documents("task", case)
        assert expected_violation(case) in pinned_violations(case["base"], mutated)

    @pytest.mark.parametrize("case", PINNED_CASES, ids=_case_id)
    def test_the_unmutated_base_breaks_nothing(self, case: contracts.InvalidCase) -> None:
        base, _ = case_documents("task", case)
        assert pinned_violations(case["base"], base) == ()

    @pytest.mark.parametrize(
        "path", contracts.task_declaration_paths(), ids=lambda p: p.name,
    )
    def test_every_published_declaration_satisfies_its_pinned_invariants(
        self, path: Path,
    ) -> None:
        assert pinned_violations(path.name, load_document(path)) == ()

    def test_the_pinned_table_names_only_declarations_that_exist(self) -> None:
        published = {p.name for p in contracts.task_declaration_paths()}
        assert set(PINNED_REQUIREMENTS) <= published

    def test_the_unpinned_declaration_is_deliberate(self) -> None:
        # refund_approval is the contract's worked example and is pinned
        # to nothing on purpose. Stated as an assertion so that stays a
        # decision rather than an omission nobody noticed.
        published = {p.name for p in contracts.task_declaration_paths()}
        assert published - set(PINNED_REQUIREMENTS) == {"refund_approval.v1.yaml"}


class TestUnclaimedStages:
    """The runtime-artifact semantics this suite does not yet cover.

    Nineteen cases across three tables. They need a validator for a
    foreign document checked against a task index — undeclared scopes,
    unregistered declaration versions, reference/hash disagreement,
    identity drift, illegal motion ordering — which is real logic, not a
    schema pass, and it does not exist in Python yet.

    The ratchet that keeps this honest: the JS validator covering these
    is not deleted until they are claimed here. Counts pinned so the gap
    stays measured.
    """

    @pytest.mark.parametrize(
        ("kind", "count"), [("decision", 6), ("event", 10), ("evidence", 3)],
    )
    def test_runtime_artifact_semantic_cases_are_unclaimed(
        self, kind: str, count: int,
    ) -> None:
        assert len(contracts.invalid_cases(kind, stage=f"{kind}_semantic")) == count

    def test_task_semantic_cases_belong_to_the_conformance_suite(self) -> None:
        # Not unclaimed — claimed elsewhere, with codes, because a task
        # declaration is a document the runtime really does load.
        assert len(contracts.invalid_cases("task", stage="semantic")) == 11
