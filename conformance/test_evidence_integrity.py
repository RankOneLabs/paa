"""Content-addressed evidence against the published artifacts.

Evidence binding is the part of PAA that has to be exactly right or the
audit trail means nothing: a motion names its evidence by SHA-256, and
that digest is re-verified when the motion is approved. So the claims
here are about bytes, not shapes — that this implementation computes the
same content address the published corpus records, and that it fails
closed when the bytes do not match the address they are filed under.
"""

from __future__ import annotations

import json
from pathlib import Path

import paa_contracts as contracts
import pytest
from jsonschema import Draft202012Validator

from paa_runtime import (
    EvidenceError,
    compute_sha256,
    evidence_ref_for,
    store_evidence,
    verify_evidence,
)

EVIDENCE_RECORDS_ROOT = contracts.RUNTIME_FIXTURES_ROOT / "evidence-records"
DECISION_ARTIFACTS_ROOT = contracts.RUNTIME_FIXTURES_ROOT / "decision-artifacts"

ALL_ARTIFACTS = (
    *((p, EVIDENCE_RECORDS_ROOT) for p in contracts.evidence_record_paths()),
    *((p, DECISION_ARTIFACTS_ROOT) for p in contracts.decision_artifact_paths()),
)


def _sha_from_path(path: Path) -> str:
    # .../evidence/paa/<sha256>/evidence.json
    return path.parent.name


class TestCorpusIsPresent:
    def test_evidence_records_are_discoverable(self) -> None:
        assert len(contracts.evidence_record_paths()) == 3

    def test_decision_artifacts_are_discoverable(self) -> None:
        assert len(contracts.decision_artifact_paths()) == 5

    def test_payload_schemas_are_discoverable(self) -> None:
        assert len(contracts.payload_schema_paths()) == 2


class TestContentAddressing:
    """The digest this implementation computes is the one on the tin."""

    @pytest.mark.parametrize(
        "path", [p for p, _ in ALL_ARTIFACTS], ids=lambda p: p.parent.name[:12],
    )
    def test_artifact_hashes_to_the_directory_it_is_filed_under(self, path: Path) -> None:
        assert compute_sha256(path.read_bytes()) == _sha_from_path(path)

    @pytest.mark.parametrize(
        "path", [p for p, _ in ALL_ARTIFACTS], ids=lambda p: p.parent.name[:12],
    )
    def test_evidence_ref_reproduces_the_published_layout(self, path: Path) -> None:
        sha = _sha_from_path(path)
        assert evidence_ref_for(sha) == f"evidence/paa/{sha}/evidence.json"

    @pytest.mark.parametrize(
        ("path", "root"), ALL_ARTIFACTS, ids=lambda v: getattr(v, "name", "")[:12],
    )
    def test_verify_evidence_accepts_the_published_artifact(
        self, path: Path, root: Path,
    ) -> None:
        sha = _sha_from_path(path)
        assert verify_evidence(evidence_ref_for(sha), sha, root=root) == path.read_bytes()

    @pytest.mark.parametrize(
        "path", [p for p, _ in ALL_ARTIFACTS], ids=lambda p: p.parent.name[:12],
    )
    def test_storing_a_published_artifact_re_derives_its_address(
        self, path: Path, tmp_path: Path,
    ) -> None:
        # Handed only the bytes, the runtime must arrive at the same
        # address the corpus files them under.
        ref, sha = store_evidence(path.read_bytes(), root=tmp_path)
        assert sha == _sha_from_path(path)
        assert ref == evidence_ref_for(sha)


class TestTamperDetection:
    """The fixture that exists so fail-closed has something real to fail on."""

    SHA = "cd85082f0ef66db18183acd3e350668078d31cbb6ff68683a9efcb552157662c"

    def test_the_tampered_fixture_really_is_mismatched(self) -> None:
        # Guards the test below: if the fixture were ever repaired, that
        # test would pass for the wrong reason.
        path = contracts.TAMPERED_EVIDENCE_ROOT / evidence_ref_for(self.SHA)
        assert path.is_file()
        assert compute_sha256(path.read_bytes()) != self.SHA

    def test_verify_evidence_rejects_tampered_bytes(self) -> None:
        with pytest.raises(EvidenceError, match="tampered with"):
            verify_evidence(
                evidence_ref_for(self.SHA), self.SHA,
                root=contracts.TAMPERED_EVIDENCE_ROOT,
            )

    def test_the_same_address_verifies_against_the_untampered_corpus(self) -> None:
        # Same ref, same digest, different root: the reference is fine and
        # it is the bytes that are wrong.
        assert verify_evidence(
            evidence_ref_for(self.SHA), self.SHA, root=EVIDENCE_RECORDS_ROOT,
        )

    def test_a_missing_artifact_fails_closed(self, tmp_path: Path) -> None:
        with pytest.raises(EvidenceError, match="missing"):
            verify_evidence(evidence_ref_for(self.SHA), self.SHA, root=tmp_path)

    def test_a_digest_disagreeing_with_its_ref_fails_closed(self) -> None:
        with pytest.raises(EvidenceError, match="does not match recorded"):
            verify_evidence(evidence_ref_for(self.SHA), "0" * 64, root=EVIDENCE_RECORDS_ROOT)


class TestPayloadSchemas:
    """The companion schemas that narrow the evidence envelope."""

    @pytest.mark.parametrize(
        "path", contracts.payload_schema_paths(), ids=lambda p: p.name,
    )
    def test_payload_schema_is_a_valid_schema(self, path: Path) -> None:
        Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))


class TestUnownedStages:
    """Runtime-artifact semantics this implementation does not validate.

    The ``*_semantic`` stages describe rules for validating a *foreign*
    document — an evidence record, decision artifact, or event sequence
    that arrived from somewhere else — against a task index: undeclared
    scopes, unregistered declaration versions, identity drift, reference
    hash disagreement, illegal motion ordering.

    ``paa_runtime`` has no such import path. It governs motions it writes
    itself, and every one of those rules is enforced at write time by the
    lifecycle API and the store's append-only triggers rather than by
    inspecting a finished document. Nineteen cases therefore have no
    validation entry point here to be pointed at, and inventing a
    document validator purely to consume them would be writing an
    implementation to fit its own test.

    The counts are pinned so the gap stays a decision rather than an
    oversight: if the runtime later grows an import path, these fail and
    the cases get claimed.
    """

    def test_evidence_semantic_cases_are_unclaimed(self) -> None:
        assert len(contracts.invalid_cases("evidence", stage="evidence_semantic")) == 3

    def test_decision_semantic_cases_are_unclaimed(self) -> None:
        assert len(contracts.invalid_cases("decision", stage="decision_semantic")) == 6

    def test_event_semantic_cases_are_unclaimed(self) -> None:
        assert len(contracts.invalid_cases("event", stage="event_semantic")) == 10

    @pytest.mark.parametrize("kind", ["evidence", "decision", "event"])
    def test_structural_cases_are_left_to_the_sites_validator(self, kind: str) -> None:
        assert contracts.case_stages(kind) == (f"{kind}_semantic", "structural")
