"""Tests for the paa_contracts accessor surface.

The recurring theme: a contract-artifact package fails most dangerously by
resolving to *nothing*. A conformance suite handed an empty fixture tuple
iterates zero times and reports success, so "the corpus is non-empty" is
asserted here rather than assumed by every downstream consumer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import paa_contracts as contracts


class TestDataResolution:
    def test_data_source_is_one_of_the_two_supported_modes(self) -> None:
        assert contracts.DATA_SOURCE in {"packaged", "worktree"}

    def test_contracts_root_exists(self) -> None:
        assert contracts.CONTRACTS_ROOT.is_dir()

    def test_resolution_requires_every_artifact_root(self, tmp_path: Path) -> None:
        # A root carrying only some of the corpus must not be accepted. If it
        # were, the missing trees' accessors would return () and a conformance
        # suite would iterate nothing and report success.
        (tmp_path / "schemas").mkdir()
        assert contracts._missing_roots(tmp_path) == (
            "examples/paa-tasks",
            "examples/runtime-conformance",
        )

    def test_the_resolved_root_is_complete(self) -> None:
        assert contracts._missing_roots(contracts.CONTRACTS_ROOT) == ()

    @pytest.mark.parametrize(
        "root",
        [
            contracts.SCHEMAS_ROOT,
            contracts.EXAMPLES_ROOT,
            contracts.TASK_FIXTURES_ROOT,
            contracts.RUNTIME_FIXTURES_ROOT,
            contracts.TAMPERED_EVIDENCE_ROOT,
        ],
    )
    def test_every_published_root_resolves(self, root: Path) -> None:
        assert root.is_dir()


class TestSchemas:
    def test_all_four_contracts_are_present(self) -> None:
        assert len(contracts.SCHEMA_IDS) == 4

    @pytest.mark.parametrize("schema_id", contracts.SCHEMA_IDS)
    def test_schema_file_exists(self, schema_id: str) -> None:
        assert contracts.schema_path(schema_id).is_file()

    @pytest.mark.parametrize("schema_id", contracts.SCHEMA_IDS)
    def test_schema_parses_and_declares_its_identity(self, schema_id: str) -> None:
        schema = contracts.load_schema(schema_id)
        assert schema["$id"] == f"https://paa.dev/{schema_id}.schema.json"

    @pytest.mark.parametrize("schema_id", contracts.SCHEMA_IDS)
    def test_schema_version_is_namespaced_by_its_own_id(self, schema_id: str) -> None:
        # `paa-task/0.2.1-draft`, never a bare version — the family name is
        # half the meaning, and it is what a consumer pins against.
        assert contracts.schema_version(schema_id).startswith(f"{schema_id}/")

    def test_unknown_contract_is_rejected(self) -> None:
        with pytest.raises(KeyError, match="unknown contract"):
            contracts.schema_path("paa-nonexistent")  # type: ignore[arg-type]


class TestPositiveFixtures:
    @pytest.mark.parametrize(
        ("accessor", "expected_count"),
        [
            (contracts.task_declaration_paths, 4),
            (contracts.autonomy_event_paths, 5),
            (contracts.evidence_record_paths, 3),
            (contracts.decision_artifact_paths, 5),
            (contracts.payload_schema_paths, 2),
        ],
    )
    def test_corpus_size_is_pinned(self, accessor: object, expected_count: int) -> None:
        # Pinned rather than merely non-empty: a fixture silently dropped from
        # the corpus weakens every conformance claim built on it without
        # failing anything, which is the quiet version of the empty-corpus bug.
        assert len(accessor()) == expected_count  # type: ignore[operator]

    @pytest.mark.parametrize(
        "accessor",
        [
            contracts.task_declaration_paths,
            contracts.autonomy_event_paths,
            contracts.evidence_record_paths,
            contracts.decision_artifact_paths,
            contracts.payload_schema_paths,
        ],
    )
    def test_every_fixture_path_is_a_readable_file(self, accessor: object) -> None:
        paths = accessor()  # type: ignore[operator]
        assert all(path.is_file() for path in paths)

    def test_autonomy_event_fixtures_are_event_arrays(self) -> None:
        for path in contracts.autonomy_event_paths():
            events = json.loads(path.read_text(encoding="utf-8"))
            assert isinstance(events, list) and events, path.name

    def test_task_fixtures_are_yaml_not_the_invalid_case_table(self) -> None:
        names = {path.name for path in contracts.task_declaration_paths()}
        assert all(name.endswith(".yaml") for name in names)
        assert "cases.json" not in names


class TestInvalidCases:
    @pytest.mark.parametrize(
        ("kind", "expected_count"),
        [("task", 63), ("evidence", 6), ("decision", 11), ("event", 15)],
    )
    def test_case_table_size_is_pinned(self, kind: str, expected_count: int) -> None:
        assert len(contracts.invalid_cases(kind)) == expected_count  # type: ignore[arg-type]

    @pytest.mark.parametrize("kind", contracts.CASE_KINDS)
    def test_every_case_has_the_universal_shape(self, kind: str) -> None:
        for case in contracts.invalid_cases(kind):  # type: ignore[arg-type]
            assert set(case) == {"id", "base", "mutations", "expected"}, case["id"]
            assert {"stage", "code", "path"} <= set(case["expected"]), case["id"]
            assert case["mutations"], case["id"]

    @pytest.mark.parametrize("kind", contracts.CASE_KINDS)
    def test_every_case_base_resolves_to_a_real_fixture(self, kind: str) -> None:
        # The strongest check in this file: it walks all 95 published cases
        # and proves each one names a positive fixture that exists at the
        # path its kind implies. A wrong base root would silently mutate the
        # wrong artifact and still fail downstream, for the wrong reason.
        for case in contracts.invalid_cases(kind):  # type: ignore[arg-type]
            assert contracts.resolve_case_base(kind, case).is_file(), case["id"]  # type: ignore[arg-type]

    @pytest.mark.parametrize("kind", contracts.CASE_KINDS)
    def test_case_ids_are_unique_within_a_table(self, kind: str) -> None:
        ids = [case["id"] for case in contracts.invalid_cases(kind)]  # type: ignore[arg-type]
        assert len(ids) == len(set(ids))

    @pytest.mark.parametrize(
        ("kind", "expected_stages"),
        [
            ("task", ("pinned", "semantic", "structural")),
            ("evidence", ("evidence_semantic", "structural")),
            ("decision", ("decision_semantic", "structural")),
            ("event", ("event_semantic", "structural")),
        ],
    )
    def test_stage_vocabulary_is_pinned(self, kind: str, expected_stages: tuple[str, ...]) -> None:
        # The stage vocabulary is an ownership boundary between this repo's
        # Ajv validator and an implementation's conformance suite. A new
        # stage appearing unannounced means some cases belong to nobody, so
        # it fails here rather than being quietly filtered out downstream.
        assert contracts.case_stages(kind) == expected_stages  # type: ignore[arg-type]

    def test_stage_filter_selects_only_that_stage(self) -> None:
        selected = contracts.invalid_cases("event", stage="event_semantic")
        assert selected
        assert all(case["expected"]["stage"] == "event_semantic" for case in selected)

    def test_stage_filter_partitions_the_table(self) -> None:
        total = len(contracts.invalid_cases("task"))
        by_stage = sum(
            len(contracts.invalid_cases("task", stage=stage))
            for stage in contracts.case_stages("task")
        )
        assert by_stage == total

    def test_unknown_stage_is_rejected(self) -> None:
        # Filtering to () would mean a one-character typo runs zero cases and
        # passes. Pinning this package's vocabulary constrains what it
        # publishes, not what a caller types — and the caller is where the
        # typo happens.
        with pytest.raises(KeyError, match="no 'event' cases carry stage"):
            contracts.invalid_cases("event", stage="event_semantics")

    def test_a_stage_valid_for_another_kind_is_still_rejected(self) -> None:
        # `semantic` is a real stage — on the task table. Asking the event
        # table for it is the plausible version of the mistake above.
        with pytest.raises(KeyError, match="no 'event' cases carry stage"):
            contracts.invalid_cases("event", stage="semantic")

    def test_unknown_case_kind_is_rejected(self) -> None:
        with pytest.raises(KeyError, match="unknown case kind"):
            contracts.invalid_cases("nope")  # type: ignore[arg-type]


class TestMutationVariants:
    """Each mutation kind carries exactly the payload its variant requires."""

    @pytest.mark.parametrize("kind", contracts.CASE_KINDS)
    def test_every_mutation_matches_its_variant(self, kind: str) -> None:
        required = {"copy": "from", "remove": None, "set": "value"}
        for case in contracts.invalid_cases(kind):  # type: ignore[arg-type]
            for mutation in case["mutations"]:
                assert mutation["kind"] in required, case["id"]
                expected_key = required[mutation["kind"]]
                extra = set(mutation) - {"kind", "path"}
                assert extra == ({expected_key} if expected_key else set()), case["id"]


class TestContentAddressing:
    """The published corpus is addressed by *raw file bytes*, not just by
    canonical bytes — and a conformance suite depends on that being true.

    The site computes content addresses over canonicalized JSON. For these
    fixtures the on-disk bytes already are the canonical bytes, so the two
    agree. That agreement is what lets an implementation verify the corpus
    with a plain sha256 of the file, which is exactly what
    ``paa_runtime.evidence.verify_evidence`` does; it has no canonicalizer
    and deliberately never will.

    So a reformatting commit — a pretty-printer, a trailing newline — would
    not corrupt the fixtures in the site's eyes but would break every
    implementation that reads them as bytes. It fails here instead.
    """

    @pytest.mark.parametrize(
        "accessor",
        [contracts.evidence_record_paths, contracts.decision_artifact_paths],
    )
    def test_raw_bytes_hash_to_the_address_the_fixture_is_filed_under(
        self, accessor: object
    ) -> None:
        import hashlib

        for path in accessor():  # type: ignore[operator]
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            assert actual == path.parent.name, path


class TestTamperedFixture:
    def test_tampered_artifact_is_outside_the_positive_corpus(self) -> None:
        # It is filed under a real evidence record's content address. If it
        # ever landed inside evidence-records/, the discovery walk would pick
        # it up and the positive corpus would contain a known-bad artifact.
        positives = set(contracts.evidence_record_paths())
        tampered = set(contracts.TAMPERED_EVIDENCE_ROOT.rglob("evidence.json"))
        assert tampered
        assert not (tampered & positives)

    def test_tampered_bytes_do_not_hash_to_the_address_they_are_filed_under(self) -> None:
        import hashlib

        for path in contracts.TAMPERED_EVIDENCE_ROOT.rglob("evidence.json"):
            claimed = path.parent.name
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            assert actual != claimed, "the tamper fixture is no longer tampered"
