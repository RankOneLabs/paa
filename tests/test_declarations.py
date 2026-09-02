"""Access-layer tests for paa_runtime.declarations.

Ported from the source consumer's declaration tests. Only the access-layer
concern comes with the package — loader output, transition extraction,
vocabulary, deployment values, evaluator-version resolution,
filename/identity invariants, and fail-closed error handling. The
schema-stage conformance class (``TestSchemaConformance``, marked
``paa_contract``) and the checkout-discovery harness do not port: that harness is
being replaced separately (paa-contracts), and this package ships no
conftest.

The source declaration loader used to read from a checked-in
``contracts/paa/`` directory and resolve evaluators against a
module-global ``PRODUCER_REGISTRY``. Both are now supplied by the
caller, so these tests build small declaration fixtures under
``tmp_path`` and define a local registry tuple instead of reading
the source repository.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import yaml

import paa_runtime.declarations as paa_declarations
from paa_runtime.declarations import (
    PAA_DECLARATION_CODES,
    PaaDeclarationError,
    PaaEvaluationBasis,
    ProducerRegistration,
    get_paa_declaration,
    load_paa_declarations,
)

_REGISTRY: tuple[ProducerRegistration, ...] = (
    ProducerRegistration(
        property="content_invariants", target="output", technique="deterministic",
        evaluation_basis=PaaEvaluationBasis(kind="invariant", ref="content_invariants"),
        epistemic_status="ground_truth",
        version="1", authority="blocking", status="implemented",
    ),
    ProducerRegistration(
        property="draft_quality", target="output", technique="llm_judge",
        evaluation_basis=PaaEvaluationBasis(kind="rubric", ref="draft_quality"),
        epistemic_status="proxy",
        version="1", authority="advisory", status="future",
    ),
    # A second version of the same evaluator identity, so a versionless
    # selector has something to be ambiguous *between*.
    ProducerRegistration(
        property="draft_quality", target="output", technique="llm_judge",
        evaluation_basis=PaaEvaluationBasis(kind="rubric", ref="draft_quality"),
        epistemic_status="proxy",
        version="2", authority="advisory", status="future",
    ),
    ProducerRegistration(
        property="publish_authorization", target="process", technique="human",
        evaluation_basis=PaaEvaluationBasis(kind="human_gold", ref="publish_authorization"),
        epistemic_status="ground_truth",
        version="1", authority="blocking", status="implemented",
    ),
    ProducerRegistration(
        property="canonical_truth", target="output", technique="human",
        evaluation_basis=PaaEvaluationBasis(kind="human_gold", ref="canonical_truth"),
        epistemic_status="ground_truth",
        version="1", authority="blocking", status="future",
    ),
)

_FIXED_POSITION_POLICY: dict[str, object] = {
    "manual": "offline",
    "hitl": "blocking",
    "hotl": "async",
    "autonomous": "offline",
}

# Mirrors the source outbound_content_publish.v1.yaml: active deployment,
# a declared scopes block, a cases promotion window.
_OUTBOUND_LIKE: dict[str, object] = {
    "task": "outbound_publish",
    "version": 1,
    "deployment": "active",
    "initial_position": "hitl",
    "scopes": ["publish:bluesky", "publish:farcaster"],
    "evaluators": [
        {
            "property": "content_invariants", "target": "output",
            "technique": "deterministic",
            "evaluation_basis": {"kind": "invariant", "ref": "content_invariants"},
            "epistemic_status": "ground_truth",
            "version": "1", "authority": "blocking",
        },
        {
            "property": "publish_authorization", "target": "process",
            "technique": "human",
            "evaluation_basis": {"kind": "human_gold", "ref": "publish_authorization"},
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

# Mirrors the source inbound_reply_surfacing.v1.yaml: shadow deployment, no
# scopes block, a duration promotion window, and a "future" evaluator.
_DURATION_TASK: dict[str, object] = {
    "task": "reply_surfacing",
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
        {
            "property": "draft_quality", "target": "output",
            "technique": "llm_judge",
            "evaluation_basis": {"kind": "rubric", "ref": "draft_quality"},
            "epistemic_status": "proxy",
            "version": "1", "authority": "advisory",
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

# Mirrors the source canonical_promotion.v1.yaml: disabled deployment, no
# scopes block, a fifty-case promotion window, and a "future" evaluator.
_CASES_TASK: dict[str, object] = {
    "task": "canonical_promotion_task",
    "version": 1,
    "deployment": "disabled",
    "initial_position": "hitl",
    "evaluators": [
        {
            "property": "publish_authorization", "target": "process",
            "technique": "human",
            "evaluation_basis": {"kind": "human_gold", "ref": "publish_authorization"},
            "epistemic_status": "ground_truth",
            "version": "1", "authority": "blocking",
        },
        {
            "property": "canonical_truth", "target": "output",
            "technique": "human",
            "evaluation_basis": {"kind": "human_gold", "ref": "canonical_truth"},
            "epistemic_status": "ground_truth",
            "version": "1", "authority": "blocking",
        },
    ],
    "position_policy": dict(_FIXED_POSITION_POLICY),
    "promotion": {
        "from": "hitl", "to": "hotl", "report": "audit_report",
        "window": {"kind": "cases", "size": 50}, "execution": "operator_approval",
    },
    "demotion": {
        "from": "hotl", "to": "hitl", "trigger": "operator_decision_or_policy_failure",
        "window": {"kind": "cases", "size": 1},
    },
}

_EXPECTED_DECLARATIONS: dict[str, dict[str, object]] = {
    "outbound_publish": {"version": 1, "deployment": "active", "initial_position": "hitl"},
    "reply_surfacing": {"version": 1, "deployment": "shadow", "initial_position": "hitl"},
    "canonical_promotion_task": {
        "version": 1, "deployment": "disabled", "initial_position": "hitl",
    },
}


def _write_declaration(directory: Path, document: dict[str, object]) -> Path:
    """Write *document* as ``<task>.v<version>.yaml`` under *directory*.

    Mirrors the source suite's versioned-declaration technique for building an
    isolated one-file declarations directory in a test.
    """
    path = directory / f"{document['task']}.v{document['version']}.yaml"
    path.write_text(yaml.safe_dump(document))
    return path


@pytest.fixture
def declarations_dir(tmp_path: Path) -> Path:
    for document in (_OUTBOUND_LIKE, _DURATION_TASK, _CASES_TASK):
        _write_declaration(tmp_path, document)
    return tmp_path


# ---------------------------------------------------------------------------
# Loader output
# ---------------------------------------------------------------------------


class TestLoaderOutput:
    def test_loads_exactly_the_expected_tasks(self, declarations_dir: Path) -> None:
        declarations = load_paa_declarations(declarations_dir, registry=_REGISTRY)
        assert set(declarations) == set(_EXPECTED_DECLARATIONS)

    @pytest.mark.parametrize("task", sorted(_EXPECTED_DECLARATIONS))
    def test_declaration_access_fields_match_the_final_spec(
        self, declarations_dir: Path, task: str,
    ) -> None:
        declaration = get_paa_declaration(task, directory=declarations_dir, registry=_REGISTRY)
        expected = _EXPECTED_DECLARATIONS[task]
        assert declaration.task == task
        assert declaration.version == expected["version"]
        assert declaration.deployment == expected["deployment"]
        assert declaration.initial_position == expected["initial_position"]

    def test_unknown_task_raises(self, declarations_dir: Path) -> None:
        with pytest.raises(PaaDeclarationError, match="no PAA declaration"):
            get_paa_declaration("does_not_exist", directory=declarations_dir, registry=_REGISTRY)

    @pytest.mark.parametrize("task", sorted(_EXPECTED_DECLARATIONS))
    def test_bare_placements_parse_to_a_default_with_no_overrides(
        self, declarations_dir: Path, task: str,
    ) -> None:
        # These fixtures author every position in the bare form, which the
        # contract treats as one mode for every declared evaluator.
        declaration = get_paa_declaration(task, directory=declarations_dir, registry=_REGISTRY)
        policy = declaration.position_policy
        assert policy.declared_positions == ("autonomous", "hitl", "hotl", "manual")
        assert policy["manual"].default == "offline"
        assert policy["hitl"].default == "blocking"
        assert policy["hotl"].default == "async"
        assert policy["autonomous"].default == "offline"
        assert all(policy[p].overrides == () for p in policy.declared_positions)


class TestTransitionExtraction:
    def test_outbound_promotion_and_demotion(self, declarations_dir: Path) -> None:
        d = get_paa_declaration("outbound_publish", directory=declarations_dir, registry=_REGISTRY)
        assert d.promotion.from_position == "hitl"
        assert d.promotion.to_position == "hotl"
        assert d.promotion.report == "outbound_publish_promotion_report"
        assert d.promotion.window.kind == "cases"
        assert d.promotion.window.size == 50
        assert d.promotion.execution == "operator_approval"
        assert d.demotion.from_position == "hotl"
        assert d.demotion.to_position == "hitl"
        assert d.demotion.trigger == "operator_decision_or_policy_failure"
        assert d.demotion.window.kind == "cases"
        assert d.demotion.window.size == 1

    def test_duration_task_promotion_uses_a_duration_window(self, declarations_dir: Path) -> None:
        d = get_paa_declaration("reply_surfacing", directory=declarations_dir, registry=_REGISTRY)
        assert d.promotion.report == "phase1_audit"
        assert d.promotion.window.kind == "duration"
        assert d.promotion.window.size == "P14D"

    def test_cases_task_promotion_uses_a_fifty_case_window(self, declarations_dir: Path) -> None:
        d = get_paa_declaration(
            "canonical_promotion_task", directory=declarations_dir, registry=_REGISTRY,
        )
        assert d.promotion.window.kind == "cases"
        assert d.promotion.window.size == 50


class TestVocabulary:
    @pytest.mark.parametrize("task", sorted(_EXPECTED_DECLARATIONS))
    def test_positions_and_deployment_are_in_the_pinned_vocabulary(
        self, declarations_dir: Path, task: str,
    ) -> None:
        d = get_paa_declaration(task, directory=declarations_dir, registry=_REGISTRY)
        assert d.initial_position in ("manual", "hitl", "hotl", "autonomous")
        assert d.deployment in ("active", "shadow", "disabled")
        assert d.promotion.from_position in ("manual", "hitl", "hotl", "autonomous")
        assert d.promotion.to_position in ("manual", "hitl", "hotl", "autonomous")
        assert d.demotion.from_position in ("manual", "hitl", "hotl", "autonomous")
        assert d.demotion.to_position in ("manual", "hitl", "hotl", "autonomous")

    def test_deployment_values_match_the_final_spec(self, declarations_dir: Path) -> None:
        assert get_paa_declaration(
            "outbound_publish", directory=declarations_dir, registry=_REGISTRY,
        ).deployment == "active"
        assert get_paa_declaration(
            "reply_surfacing", directory=declarations_dir, registry=_REGISTRY,
        ).deployment == "shadow"
        assert get_paa_declaration(
            "canonical_promotion_task", directory=declarations_dir, registry=_REGISTRY,
        ).deployment == "disabled"


class TestEvaluatorVersionResolution:
    def _evaluator_tuples(
        self, declarations_dir: Path, task: str,
    ) -> set[tuple[str, str, str, str, str, str, str, str]]:
        d = get_paa_declaration(task, directory=declarations_dir, registry=_REGISTRY)
        return {
            (
                e.property, e.target, e.technique, e.evaluation_basis.kind,
                e.evaluation_basis.ref, e.epistemic_status, e.version, e.authority,
            )
            for e in d.evaluators
        }

    def test_every_declared_evaluator_resolves_against_the_supplied_registry(
        self, declarations_dir: Path,
    ) -> None:
        registry_tuples = {
            (
                r.property, r.target, r.technique, r.evaluation_basis.kind,
                r.evaluation_basis.ref, r.epistemic_status, r.version, r.authority,
            )
            for r in _REGISTRY
        }
        for task in _EXPECTED_DECLARATIONS:
            for evaluator_tuple in self._evaluator_tuples(declarations_dir, task):
                assert evaluator_tuple in registry_tuples, (
                    f"{task}: evaluator {evaluator_tuple} not in registry"
                )

    def test_outbound_publish_evaluators(self, declarations_dir: Path) -> None:
        assert self._evaluator_tuples(declarations_dir, "outbound_publish") == {
            (
                "content_invariants", "output", "deterministic", "invariant",
                "content_invariants", "ground_truth", "1", "blocking",
            ),
            (
                "publish_authorization", "process", "human", "human_gold",
                "publish_authorization", "ground_truth", "1", "blocking",
            ),
        }

    def test_status_future_entry_still_resolves(self, declarations_dir: Path) -> None:
        # reply_surfacing declares draft_quality, which is registered
        # "future" — loading must succeed rather than fail closed.
        d = get_paa_declaration("reply_surfacing", directory=declarations_dir, registry=_REGISTRY)
        assert (
            "draft_quality", "output", "llm_judge", "rubric",
            "draft_quality", "proxy", "1", "advisory",
        ) in {
            (
                e.property, e.target, e.technique, e.evaluation_basis.kind,
                e.evaluation_basis.ref, e.epistemic_status, e.version, e.authority,
            )
            for e in d.evaluators
        }

    def test_evaluator_absent_from_supplied_registry_fails_closed(
        self, declarations_dir: Path,
    ) -> None:
        # A registry that omits publish_authorization entirely: the
        # outbound declaration references it, so loading must fail
        # closed even though the evaluator entry itself is well-formed.
        narrow_registry = tuple(r for r in _REGISTRY if r.property != "publish_authorization")
        with pytest.raises(PaaDeclarationError, match="does not resolve"):
            load_paa_declarations(declarations_dir, registry=narrow_registry)


# ---------------------------------------------------------------------------
# Fail-closed loader behavior
# ---------------------------------------------------------------------------


class TestFailClosed:
    def test_missing_directory_raises(self, tmp_path: Path) -> None:
        with pytest.raises(PaaDeclarationError, match="not found"):
            load_paa_declarations(tmp_path / "does_not_exist", registry=_REGISTRY)

    def test_empty_directory_raises(self, tmp_path: Path) -> None:
        with pytest.raises(PaaDeclarationError, match="no PAA task declarations"):
            load_paa_declarations(tmp_path, registry=_REGISTRY)

    def test_duplicate_task_across_files_raises(self, tmp_path: Path) -> None:
        base = dict(_CASES_TASK)
        _write_declaration(tmp_path, base)
        (tmp_path / "canonical_promotion_task_dupe.yaml").write_text(yaml.safe_dump(base))
        with pytest.raises(PaaDeclarationError, match="duplicate PAA declaration"):
            load_paa_declarations(tmp_path, registry=_REGISTRY)

    def test_missing_required_field_raises(self, tmp_path: Path) -> None:
        base = dict(_CASES_TASK)
        del base["demotion"]
        _write_declaration(tmp_path, base)
        with pytest.raises(PaaDeclarationError, match="missing required field"):
            load_paa_declarations(tmp_path, registry=_REGISTRY)

    def test_unsupported_deployment_raises(self, tmp_path: Path) -> None:
        base = dict(_CASES_TASK)
        base["deployment"] = "wat"
        _write_declaration(tmp_path, base)
        with pytest.raises(PaaDeclarationError, match="unsupported deployment"):
            load_paa_declarations(tmp_path, registry=_REGISTRY)

    def test_unsupported_initial_position_raises(self, tmp_path: Path) -> None:
        base = dict(_CASES_TASK)
        base["initial_position"] = "wat"
        _write_declaration(tmp_path, base)
        with pytest.raises(PaaDeclarationError, match="unsupported"):
            load_paa_declarations(tmp_path, registry=_REGISTRY)

    def test_unresolved_evaluator_raises(self, tmp_path: Path) -> None:
        base = dict(_CASES_TASK)
        evaluators = [dict(e) for e in base["evaluators"]]  # type: ignore[union-attr]
        evaluators[0]["version"] = "999"
        base["evaluators"] = evaluators
        _write_declaration(tmp_path, base)
        with pytest.raises(PaaDeclarationError, match="does not resolve"):
            load_paa_declarations(tmp_path, registry=_REGISTRY)

    def test_non_mapping_declaration_raises(self, tmp_path: Path) -> None:
        (tmp_path / "broken.v1.yaml").write_text("- not\n- a\n- mapping\n")
        with pytest.raises(PaaDeclarationError, match="must be a mapping"):
            load_paa_declarations(tmp_path, registry=_REGISTRY)

    def test_missing_position_policy_raises(self, tmp_path: Path) -> None:
        base = dict(_CASES_TASK)
        del base["position_policy"]
        _write_declaration(tmp_path, base)
        with pytest.raises(PaaDeclarationError, match="missing required field"):
            load_paa_declarations(tmp_path, registry=_REGISTRY)

    def test_position_policy_omitting_a_referenced_position_raises(
        self, tmp_path: Path,
    ) -> None:
        # hotl is this declaration's promotion.to and demotion.from, so
        # dropping it leaves transitions pointing at a position the
        # declaration never declares.
        base = dict(_CASES_TASK)
        policy = dict(base["position_policy"])  # type: ignore[arg-type]
        del policy["hotl"]
        base["position_policy"] = policy
        _write_declaration(tmp_path, base)
        with pytest.raises(PaaDeclarationError, match="is not declared in position_policy"):
            load_paa_declarations(tmp_path, registry=_REGISTRY)

    def test_position_policy_may_declare_a_subset_of_positions(self, tmp_path: Path) -> None:
        # The contract admits any non-empty subset, and this declaration
        # only ever references hitl and hotl. The fixed four-position table
        # this loader used to enforce could not express that.
        base = dict(_CASES_TASK)
        base["position_policy"] = {"hitl": "blocking", "hotl": "async"}
        _write_declaration(tmp_path, base)
        declarations = load_paa_declarations(tmp_path, registry=_REGISTRY)
        policy = declarations["canonical_promotion_task"].position_policy
        assert policy.declared_positions == ("hitl", "hotl")

    def test_position_policy_unsupported_mode_raises(self, tmp_path: Path) -> None:
        base = dict(_CASES_TASK)
        policy = dict(base["position_policy"])  # type: ignore[arg-type]
        policy["hotl"] = "wat"
        base["position_policy"] = policy
        _write_declaration(tmp_path, base)
        with pytest.raises(
            PaaDeclarationError, match="position_policy 'hotl' placement is unsupported",
        ):
            load_paa_declarations(tmp_path, registry=_REGISTRY)

    def test_position_policy_unknown_position_raises(self, tmp_path: Path) -> None:
        base = dict(_CASES_TASK)
        policy = dict(base["position_policy"])  # type: ignore[arg-type]
        policy["supervised"] = "blocking"
        base["position_policy"] = policy
        _write_declaration(tmp_path, base)
        with pytest.raises(PaaDeclarationError, match="unsupported position"):
            load_paa_declarations(tmp_path, registry=_REGISTRY)

    def test_promotion_between_identical_positions_raises(self, tmp_path: Path) -> None:
        base = dict(_CASES_TASK)
        base["promotion"] = dict(base["promotion"]) | {"from": "hotl"}  # type: ignore[operator]
        _write_declaration(tmp_path, base)
        with pytest.raises(PaaDeclarationError, match="promotion.from and promotion.to are both"):
            load_paa_declarations(tmp_path, registry=_REGISTRY)


# ---------------------------------------------------------------------------
# Change 1: registry is deleted and injected — no module-global fallback
# ---------------------------------------------------------------------------


def test_producer_registry_is_not_a_module_global() -> None:
    assert not hasattr(paa_declarations, "PRODUCER_REGISTRY")
    assert "PRODUCER_REGISTRY" not in paa_declarations.__all__


# ---------------------------------------------------------------------------
# Change 2: DEFAULT_DECLARATIONS_DIR is deleted — directory is required
# ---------------------------------------------------------------------------


def test_default_declarations_dir_is_deleted() -> None:
    assert not hasattr(paa_declarations, "DEFAULT_DECLARATIONS_DIR")
    assert "DEFAULT_DECLARATIONS_DIR" not in paa_declarations.__all__


# ---------------------------------------------------------------------------
# Change 3: the optional 'scopes' field
# ---------------------------------------------------------------------------


class TestScopes:
    def test_absent_scopes_is_none(self, declarations_dir: Path) -> None:
        d = get_paa_declaration(
            "canonical_promotion_task", directory=declarations_dir, registry=_REGISTRY,
        )
        assert d.scopes is None

    def test_present_scopes_parses_to_a_tuple(self, declarations_dir: Path) -> None:
        d = get_paa_declaration("outbound_publish", directory=declarations_dir, registry=_REGISTRY)
        assert d.scopes == ("publish:bluesky", "publish:farcaster")

    def test_explicit_null_scopes_raises_rather_than_reading_as_absent(
        self, tmp_path: Path
    ) -> None:
        """A bare ``scopes:`` in YAML parses to None. That is a malformed
        declaration under the published schema (which admits scopes only as
        an array), not a task declaring no scopes — so it must fail closed
        rather than silently resolve to the null-scope behavior."""
        base = dict(_CASES_TASK)
        base["scopes"] = None
        _write_declaration(tmp_path, base)
        with pytest.raises(PaaDeclarationError, match="'scopes' must be a non-empty list"):
            load_paa_declarations(tmp_path, registry=_REGISTRY)

    def test_scopes_not_a_list_raises(self, tmp_path: Path) -> None:
        base = dict(_CASES_TASK)
        base["scopes"] = "publish:bluesky"
        _write_declaration(tmp_path, base)
        with pytest.raises(PaaDeclarationError, match="'scopes' must be a non-empty list"):
            load_paa_declarations(tmp_path, registry=_REGISTRY)

    def test_scopes_empty_list_raises(self, tmp_path: Path) -> None:
        base = dict(_CASES_TASK)
        base["scopes"] = []
        _write_declaration(tmp_path, base)
        with pytest.raises(PaaDeclarationError, match="'scopes' must be a non-empty list"):
            load_paa_declarations(tmp_path, registry=_REGISTRY)

    def test_scopes_non_string_item_raises(self, tmp_path: Path) -> None:
        base = dict(_CASES_TASK)
        base["scopes"] = [1]
        _write_declaration(tmp_path, base)
        with pytest.raises(PaaDeclarationError, match="'scopes' entries must be non-empty strings"):
            load_paa_declarations(tmp_path, registry=_REGISTRY)

    def test_scopes_empty_string_item_raises(self, tmp_path: Path) -> None:
        base = dict(_CASES_TASK)
        base["scopes"] = [""]
        _write_declaration(tmp_path, base)
        with pytest.raises(PaaDeclarationError, match="'scopes' entries must be non-empty strings"):
            load_paa_declarations(tmp_path, registry=_REGISTRY)

    def test_scopes_duplicate_items_raise(self, tmp_path: Path) -> None:
        base = dict(_CASES_TASK)
        base["scopes"] = ["publish:bluesky", "publish:bluesky"]
        _write_declaration(tmp_path, base)
        with pytest.raises(PaaDeclarationError, match="'scopes' must not contain duplicates"):
            load_paa_declarations(tmp_path, registry=_REGISTRY)


class TestMalformedScalarTypes:
    """Every closed-vocabulary field must reject non-string YAML values as
    a declaration error.

    YAML admits a mapping or a sequence anywhere a scalar is expected, and
    both are unhashable — testing one for membership in a frozenset raises
    TypeError rather than returning False. Before the guard, a declaration
    like ``initial_position: {a: 1}`` escaped the loader as an unhandled
    TypeError from a private helper, which breaks the only promise this
    module makes about malformed input.
    """

    @pytest.mark.parametrize("bad_value", [{"a": 1}, ["a"]], ids=["mapping", "sequence"])
    def test_initial_position_non_string(self, tmp_path: Path, bad_value: object) -> None:
        base = dict(_CASES_TASK)
        base["initial_position"] = bad_value
        _write_declaration(tmp_path, base)
        with pytest.raises(PaaDeclarationError, match="initial_position"):
            load_paa_declarations(tmp_path, registry=_REGISTRY)

    def test_deployment_non_string(self, tmp_path: Path) -> None:
        base = dict(_CASES_TASK)
        base["deployment"] = {"a": 1}
        _write_declaration(tmp_path, base)
        with pytest.raises(PaaDeclarationError, match="unsupported deployment"):
            load_paa_declarations(tmp_path, registry=_REGISTRY)

    def test_window_kind_non_string(self, tmp_path: Path) -> None:
        base = dict(_CASES_TASK)
        promotion = dict(base["promotion"])  # type: ignore[arg-type]
        promotion["window"] = {"kind": {"a": 1}, "size": 50}
        base["promotion"] = promotion
        _write_declaration(tmp_path, base)
        with pytest.raises(PaaDeclarationError, match="unsupported window kind"):
            load_paa_declarations(tmp_path, registry=_REGISTRY)

    def test_promotion_execution_non_string(self, tmp_path: Path) -> None:
        base = dict(_CASES_TASK)
        promotion = dict(base["promotion"])  # type: ignore[arg-type]
        promotion["execution"] = ["operator_approval"]
        base["promotion"] = promotion
        _write_declaration(tmp_path, base)
        with pytest.raises(PaaDeclarationError, match="unsupported promotion execution"):
            load_paa_declarations(tmp_path, registry=_REGISTRY)

    def test_position_policy_mode_non_string(self, tmp_path: Path) -> None:
        base = dict(_CASES_TASK)
        policy = dict(_FIXED_POSITION_POLICY)
        policy["hitl"] = {"a": 1}
        base["position_policy"] = policy
        _write_declaration(tmp_path, base)
        with pytest.raises(PaaDeclarationError, match="position_policy"):
            load_paa_declarations(tmp_path, registry=_REGISTRY)


class TestDeclarationEncoding:
    def test_non_ascii_declaration_content_round_trips(self, tmp_path: Path) -> None:
        """Declarations are read as UTF-8 explicitly, not in the host's
        locale encoding.

        This asserts the content survives; it cannot vary the interpreter's
        preferred encoding to prove locale independence directly. The
        guarantee is carried by the explicit ``encoding="utf-8"`` at the
        read — without it a non-UTF-8 host either fails to decode this
        file or decodes it to different text.
        """
        base = dict(_CASES_TASK)
        promotion = dict(base["promotion"])  # type: ignore[arg-type]
        promotion["report"] = "promoción_señal_report"
        base["promotion"] = promotion
        path = tmp_path / f"{base['task']}.v{base['version']}.yaml"
        path.write_bytes(
            yaml.safe_dump(base, allow_unicode=True).encode("utf-8")
        )

        declarations = load_paa_declarations(tmp_path, registry=_REGISTRY)
        assert declarations[str(base["task"])].promotion.report == "promoción_señal_report"


# ---------------------------------------------------------------------------
# Placement overrides
# ---------------------------------------------------------------------------


class TestPlacementOverrides:
    """The default-plus-overrides placement form and its cross-field rules.

    A position's placement may refine the default for individual
    evaluators — a task can hold most evaluators blocking at hotl while
    letting its human gates run async. Every rule below exists because an
    override that does not resolve to exactly one evaluator, exactly once,
    and to something other than the default, is an authoring mistake whose
    effect would otherwise be silent.
    """

    def _with_hotl_overrides(self, overrides: list[dict[str, object]]) -> dict[str, object]:
        base = dict(_DURATION_TASK)
        policy = dict(base["position_policy"])  # type: ignore[arg-type]
        policy["hotl"] = {"default": "blocking", "overrides": overrides}
        base["position_policy"] = policy
        return base

    def test_override_applies_to_its_evaluator_only(self, tmp_path: Path) -> None:
        _write_declaration(tmp_path, self._with_hotl_overrides([
            {
                "selector": {"property": "draft_quality", "technique": "llm_judge"},
                "placement": "async",
            },
        ]))
        declaration = load_paa_declarations(tmp_path, registry=_REGISTRY)["reply_surfacing"]
        placement = declaration.position_policy["hotl"]
        by_property = {e.property: e for e in declaration.evaluators}

        assert placement.default == "blocking"
        assert placement.for_evaluator(by_property["draft_quality"]) == "async"
        assert placement.for_evaluator(by_property["content_invariants"]) == "blocking"

    def test_bare_placement_applies_its_mode_to_every_evaluator(self, tmp_path: Path) -> None:
        _write_declaration(tmp_path, dict(_DURATION_TASK))
        declaration = load_paa_declarations(tmp_path, registry=_REGISTRY)["reply_surfacing"]
        placement = declaration.position_policy["hotl"]
        assert placement.overrides == ()
        assert all(placement.for_evaluator(e) == "async" for e in declaration.evaluators)

    def test_override_matching_no_evaluator_raises(self, tmp_path: Path) -> None:
        _write_declaration(tmp_path, self._with_hotl_overrides([
            {
                "selector": {"property": "not_declared", "technique": "human"},
                "placement": "async",
            },
        ]))
        with pytest.raises(PaaDeclarationError, match="matches no declared evaluator"):
            load_paa_declarations(tmp_path, registry=_REGISTRY)

    def test_ambiguous_override_raises(self, tmp_path: Path) -> None:
        # Two versions of one evaluator identity, selected without a
        # version: the declaration cannot say which one it meant.
        base = self._with_hotl_overrides([
            {
                "selector": {"property": "draft_quality", "technique": "llm_judge"},
                "placement": "async",
            },
        ])
        evaluators = [dict(e) for e in base["evaluators"]]  # type: ignore[attr-defined]
        second = dict(evaluators[1])
        second["version"] = "2"
        base["evaluators"] = [*evaluators, second]
        _write_declaration(tmp_path, base)
        with pytest.raises(PaaDeclarationError, match="matches 2 declared evaluators"):
            load_paa_declarations(tmp_path, registry=_REGISTRY)

    def test_duplicate_override_raises_even_when_selectors_differ_in_text(
        self, tmp_path: Path,
    ) -> None:
        # A versionless selector and one naming that evaluator's version are
        # different text and the same evaluator.
        _write_declaration(tmp_path, self._with_hotl_overrides([
            {
                "selector": {"property": "draft_quality", "technique": "llm_judge"},
                "placement": "async",
            },
            {
                "selector": {
                    "property": "draft_quality", "technique": "llm_judge", "version": "1",
                },
                "placement": "offline",
            },
        ]))
        with pytest.raises(PaaDeclarationError, match="already overridden"):
            load_paa_declarations(tmp_path, registry=_REGISTRY)

    def test_redundant_override_raises(self, tmp_path: Path) -> None:
        _write_declaration(tmp_path, self._with_hotl_overrides([
            {
                "selector": {"property": "draft_quality", "technique": "llm_judge"},
                "placement": "blocking",
            },
        ]))
        with pytest.raises(PaaDeclarationError, match="equals the hotl default"):
            load_paa_declarations(tmp_path, registry=_REGISTRY)

    def test_empty_overrides_list_raises(self, tmp_path: Path) -> None:
        _write_declaration(tmp_path, self._with_hotl_overrides([]))
        with pytest.raises(PaaDeclarationError, match="'overrides' must be a non-empty list"):
            load_paa_declarations(tmp_path, registry=_REGISTRY)


class TestErrorCodeVocabulary:
    """No raise site may name a rule outside the published vocabulary.

    The conformance suite proves the constant matches the contract, and
    that every entry in it is emitted by something real. Neither can see
    a raise site that names a rule the constant never heard of: with no
    case targeting it, there is nothing to run that would notice.

    Reading the raise sites directly is what covers that. ``ast`` rather
    than a regex because a code split across concatenated lines is still
    a literal to the parser and not to a pattern, and reading beats
    importing here — the point is to see what the source *says*, not what
    one execution path happens to produce.
    """

    @staticmethod
    def _declared_codes() -> set[str]:
        source = Path(paa_declarations.__file__).read_text(encoding="utf-8")
        return {
            keyword.value.value
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "PaaDeclarationError"
            for keyword in node.keywords
            if keyword.arg == "code" and isinstance(keyword.value, ast.Constant)
        }

    def test_every_raise_site_code_is_in_the_published_vocabulary(self) -> None:
        assert self._declared_codes() <= PAA_DECLARATION_CODES

    def test_the_raise_sites_were_actually_found(self) -> None:
        # Guards the subset check above, which an empty set satisfies —
        # exactly what a rename of the exception class would produce.
        assert self._declared_codes() == PAA_DECLARATION_CODES

    def test_a_structural_guard_carries_no_code_or_pointer(self, tmp_path: Path) -> None:
        _write_declaration(tmp_path, {"task": "t", "version": 1})
        with pytest.raises(PaaDeclarationError) as raised:
            load_paa_declarations(tmp_path, registry=_REGISTRY)
        assert raised.value.code is None
        assert raised.value.pointer is None

    def test_a_semantic_failure_carries_both(self, tmp_path: Path) -> None:
        declaration = dict(_CASES_TASK)
        policy = dict(_FIXED_POSITION_POLICY)
        del policy["autonomous"]
        declaration["initial_position"] = "autonomous"
        declaration["position_policy"] = policy
        _write_declaration(tmp_path, declaration)

        with pytest.raises(PaaDeclarationError) as raised:
            load_paa_declarations(tmp_path, registry=_REGISTRY)
        assert raised.value.code == "initial_position.not_in_policy"
        assert raised.value.pointer == "/initial_position"
