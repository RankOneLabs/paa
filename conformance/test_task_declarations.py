"""The loader against the published task-declaration corpus.

Two claims, in both directions: this implementation accepts every valid
published declaration, and rejects every invalid case whose stage it
owns.

Stage ownership is the contract's, not a convenience. ``structural``
cases are expressed in Ajv's vocabulary — its error keywords, its JSON
pointers — and belong to the site's validator; a Python suite asserting
them would be testing a reimplementation of Ajv. ``pinned`` cases assert
facts about particular fixtures (that *this* declaration is deployment
``active``), which is corpus-pinning data no implementation carries.
What is left, ``semantic``, is the contract's own cross-field rules, and
those are exactly what a task-declaration implementation must enforce.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import paa_contracts as contracts
import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker

from conformance._mutations import apply_mutations
from conformance.conftest import build_registry
from paa_runtime import ProducerRegistration, load_paa_declarations
from paa_runtime.declarations import PaaDeclarationError

TASK_SCHEMA: dict[str, Any] = contracts.load_schema("paa-task")

#: The stages the published task table carries. Pinned here so a stage
#: the contract adds later fails this suite rather than silently falling
#: outside every filter below and being checked by nobody.
EXPECTED_STAGES = ("pinned", "semantic", "structural")

#: The one stage a task-declaration implementation owns.
OWNED_STAGE = "semantic"

SEMANTIC_CASES = contracts.invalid_cases("task", stage=OWNED_STAGE)


def _registry_for(document: dict[str, Any]) -> tuple[ProducerRegistration, ...]:
    """A registry registering exactly what *document* declares.

    Built per-case from the mutated document rather than from the corpus,
    so registry resolution cannot reject a case before the rule under
    test gets to. A case that adds a second evaluator version to create
    an ambiguous selector would otherwise fail as "does not resolve
    against the supplied producer registry" — a true statement, and the
    wrong reason, which would leave the rule it targets unexercised.
    """
    try:
        return build_registry([document])
    except (KeyError, TypeError):
        # The evaluator list is itself malformed, which is a structural
        # concern. Fall back to an empty registry and let the loader
        # reject the document on its own terms.
        return ()


def _write(directory: Path, document: dict[str, Any]) -> None:
    (directory / "declaration.v1.yaml").write_text(
        yaml.safe_dump(document), encoding="utf-8",
    )


class TestCorpusIsPresent:
    """A conformance run over an empty corpus is the failure to prevent."""

    def test_every_published_declaration_is_discoverable(self) -> None:
        assert len(contracts.task_declaration_paths()) == 4

    def test_the_case_table_carries_exactly_the_expected_stages(self) -> None:
        assert contracts.case_stages("task") == EXPECTED_STAGES

    def test_the_owned_stage_has_cases(self) -> None:
        assert len(SEMANTIC_CASES) > 0


class TestPositiveCorpus:
    """Every published declaration loads, and means what it says."""

    def test_the_loader_accepts_the_whole_published_corpus(
        self, declarations_dir: Path, registry: tuple[ProducerRegistration, ...],
    ) -> None:
        declarations = load_paa_declarations(declarations_dir, registry=registry)
        assert set(declarations) == {
            "refund_approval",
            "outbound_content_publish",
            "inbound_reply_surfacing",
            "canonical_promotion",
        }

    @pytest.mark.parametrize(
        "path", contracts.task_declaration_paths(), ids=lambda p: p.name,
    )
    def test_every_published_declaration_is_valid_against_its_schema(
        self, path: Path,
    ) -> None:
        # Not an Ajv reimplementation: this asserts the artifacts this run
        # received are the valid corpus they claim to be, which is what
        # every assertion below depends on.
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        Draft202012Validator(
            TASK_SCHEMA, format_checker=FormatChecker(),
        ).validate(document)

    def test_loaded_fields_match_the_authored_document(
        self, declarations_dir: Path, registry: tuple[ProducerRegistration, ...],
    ) -> None:
        declarations = load_paa_declarations(declarations_dir, registry=registry)

        for path in contracts.task_declaration_paths():
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
            loaded = declarations[document["task"]]

            assert loaded.version == document["version"]
            assert loaded.deployment == document["deployment"]
            assert loaded.initial_position == document["initial_position"]
            assert loaded.promotion.from_position == document["promotion"]["from"]
            assert loaded.promotion.to_position == document["promotion"]["to"]
            assert loaded.demotion.from_position == document["demotion"]["from"]
            assert loaded.demotion.to_position == document["demotion"]["to"]
            assert loaded.position_policy.declared_positions == tuple(
                sorted(document["position_policy"])
            )
            assert len(loaded.evaluators) == len(document["evaluators"])

    def test_placement_overrides_survive_the_load(
        self, declarations_dir: Path, registry: tuple[ProducerRegistration, ...],
    ) -> None:
        # outbound_content_publish is the corpus's one declaration using
        # the refined placement form: hotl blocking by default, with its
        # two human gates async. A loader that dropped overrides would
        # still pass every other assertion here.
        declaration = load_paa_declarations(declarations_dir, registry=registry)[
            "outbound_content_publish"
        ]
        hotl = declaration.position_policy["hotl"]
        by_property = {e.property: e for e in declaration.evaluators}

        assert hotl.default == "blocking"
        assert len(hotl.overrides) == 2
        assert hotl.for_evaluator(by_property["publish_authorization"]) == "async"
        assert hotl.for_evaluator(by_property["publish_quality"]) == "async"
        assert hotl.for_evaluator(by_property["outbound_content_invariants"]) == "blocking"


class TestOwnedNegativeCases:
    """Every semantic case is rejected, fail-closed."""

    @pytest.mark.parametrize("case", SEMANTIC_CASES, ids=lambda c: c["id"])
    def test_semantic_case_is_rejected(
        self, case: dict[str, Any], tmp_path: Path,
    ) -> None:
        base = yaml.safe_load(
            contracts.resolve_case_base("task", case).read_text(encoding="utf-8")
        )
        mutated = apply_mutations(base, case["mutations"])
        _write(tmp_path, mutated)

        with pytest.raises(PaaDeclarationError):
            load_paa_declarations(tmp_path, registry=_registry_for(mutated))

    @pytest.mark.parametrize("case", SEMANTIC_CASES, ids=lambda c: c["id"])
    def test_the_unmutated_base_of_each_case_still_loads(
        self, case: dict[str, Any], tmp_path: Path,
    ) -> None:
        # Without this, a loader that rejected everything would pass the
        # test above. The mutation has to be what makes the difference.
        base = yaml.safe_load(
            contracts.resolve_case_base("task", case).read_text(encoding="utf-8")
        )
        _write(tmp_path, base)
        assert load_paa_declarations(tmp_path, registry=_registry_for(base))


class TestUnownedStages:
    """What this suite deliberately does not check, and why.

    Recorded as assertions rather than prose so the boundary moves only
    when someone changes it on purpose. Both counts are load-bearing: if
    the contract reclassifies a case, these fail and the split gets
    revisited instead of quietly widening.
    """

    def test_structural_cases_are_left_to_the_sites_validator(self) -> None:
        assert len(contracts.invalid_cases("task", stage="structural")) == 37

    def test_pinned_cases_are_corpus_pinning_not_contract_rules(self) -> None:
        assert len(contracts.invalid_cases("task", stage="pinned")) == 15
