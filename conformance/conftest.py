"""Shared fixtures resolving against the published contract artifacts."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import paa_contracts as contracts
import pytest
import yaml

from paa_runtime import PaaEvaluationBasis, ProducerRegistration, RuntimeConfig


@pytest.fixture(scope="session")
def data_source() -> str:
    """Whether the artifacts came from packaged data or a site checkout.

    Both resolve to the same bytes from the same commit, so this never
    changes an answer — it is surfaced because it changes what a failure
    *means*.
    """
    return contracts.DATA_SOURCE


@pytest.fixture(scope="session")
def declarations_dir() -> Path:
    """The published task-declaration corpus, as a loadable directory.

    ``load_paa_declarations`` globs ``*.yaml``, and the invalid cases live
    under ``invalid/`` as JSON, so pointing it at the fixture root loads
    exactly the four valid declarations.
    """
    return contracts.TASK_FIXTURES_ROOT


@pytest.fixture(scope="session")
def declaration_documents() -> tuple[dict[str, Any], ...]:
    """Every published task declaration, parsed but not yet loaded."""
    return tuple(
        yaml.safe_load(path.read_text(encoding="utf-8"))
        for path in contracts.task_declaration_paths()
    )


def build_registry(documents: Sequence[dict[str, Any]]) -> tuple[ProducerRegistration, ...]:
    """A producer registry registering exactly what the corpus declares.

    The registry is consumer domain data the runtime deliberately does not
    own, so a conformance run has to supply one. Deriving it from the
    corpus is the honest choice: it makes the claim "given a registry that
    registers what these declarations reference, the loader accepts them",
    which is the claim an implementation can actually make about fixtures
    whose producers live in somebody else's codebase.

    Every entry is registered ``implemented``. The implemented/future
    split governs whether a consumer has built the producer yet — a fact
    about the consumer, not about the contract.
    """
    registrations: dict[tuple[str, ...], ProducerRegistration] = {}

    for document in documents:
        for evaluator in document["evaluators"]:
            basis = evaluator["evaluation_basis"]
            identity = (
                evaluator["property"], evaluator["target"], evaluator["technique"],
                basis["kind"], basis["ref"], evaluator["epistemic_status"],
                evaluator["version"], evaluator["authority"],
            )
            registrations.setdefault(
                identity,
                ProducerRegistration(
                    property=evaluator["property"],
                    target=evaluator["target"],
                    technique=evaluator["technique"],
                    evaluation_basis=PaaEvaluationBasis(kind=basis["kind"], ref=basis["ref"]),
                    epistemic_status=evaluator["epistemic_status"],
                    version=evaluator["version"],
                    authority=evaluator["authority"],
                    status="implemented",
                ),
            )

    return tuple(registrations.values())


@pytest.fixture(scope="session")
def registry(
    declaration_documents: tuple[dict[str, Any], ...],
) -> tuple[ProducerRegistration, ...]:
    return build_registry(declaration_documents)


@pytest.fixture
def runtime_config(
    tmp_path: Path,
    declarations_dir: Path,
    registry: tuple[ProducerRegistration, ...],
) -> RuntimeConfig:
    """A runtime rooted on the published declarations and a scratch store.

    ``evidence_root`` is a temp directory rather than the published
    fixture tree on purpose: a replay must *re-derive* each artifact's
    content address from the bytes it was handed, and pointing at the
    published tree would let an already-correct file stand in for that.
    """
    return RuntimeConfig(
        declarations_dir=declarations_dir,
        evidence_root=tmp_path / "evidence",
        registry=registry,
        db_path=tmp_path / "paa_runtime.db",
        actor_env_var="PAA_CONFORMANCE_ACTOR",
    )
