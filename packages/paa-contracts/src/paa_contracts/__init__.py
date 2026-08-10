"""Published Progressive Autonomy Architecture contract artifacts.

This package is the contract side of the PAA split: the four normative JSON
Schemas, the positive fixture corpus every implementation is checked against,
and the table-driven invalid-case matrices. It contains no runtime logic and
has no dependencies — it is data, plus honest paths to that data.

The dependency direction is the point. An implementation depends on the
contract; the contract never depends on an implementation. ``paa-runtime`` is
the first consumer, Scout's task-schema conformance test is the second, and a
second implementation in any language gets its fixtures the same way the first
one does. That is implementation-neutrality made mechanical instead of
asserted.

Nothing here is a copy. The artifacts are pulled from the paa.dev working tree
at build time by hatch_build.py, so the files the site serves at
https://www.paa.dev and the files a conformance suite loads are the same bytes
from the same commit.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, NotRequired, TypedDict

__version__ = "0.1.0"


class ContractsUnavailableError(RuntimeError):
    """The contract artifacts could not be located.

    Raised at import time rather than at first access. A conformance suite
    that silently finds no fixtures reports success over an empty corpus,
    which is the one failure mode a conformance suite must never have.
    """


SchemaId = Literal[
    "paa-task",
    "paa-evidence-record",
    "paa-decision-artifact",
    "paa-autonomy-event",
]

#: The normative contracts, in the same order as the site's
#: scripts/lib/contract-registry.mjs. Both lists exist because they serve
#: different languages; test_registry_parity.py asserts they agree, so the
#: duplication cannot drift silently.
SCHEMA_IDS: tuple[SchemaId, ...] = (
    "paa-task",
    "paa-evidence-record",
    "paa-decision-artifact",
    "paa-autonomy-event",
)

CaseKind = Literal["task", "evidence", "decision", "event"]

#: The invalid-case tables. Every case in all four shares one shape, which is
#: what lets a single accessor serve all of them.
CASE_KINDS: tuple[CaseKind, ...] = ("task", "evidence", "decision", "event")


# One edit applied to a positive fixture to produce an invalid document.
#
# Three variants, modelled separately rather than as one shape with optional
# `from` and `value`. A single permissive shape would type-check a `copy` with
# no source and a `remove` carrying a value — neither of which occurs in the
# published tables, and neither of which the code applying these mutations
# should have to defend against. Splitting them puts each variant's required
# payload in the type instead of in a comment.
#
# CopyMutation is declared functionally because `from` is a Python keyword and
# cannot be a class-syntax field; read it as `mutation["from"]`.
CopyMutation = TypedDict(
    "CopyMutation",
    {"kind": Literal["copy"], "path": str, "from": str},
)


class RemoveMutation(TypedDict):
    kind: Literal["remove"]
    path: str


class SetMutation(TypedDict):
    kind: Literal["set"]
    path: str
    value: Any


#: Discriminated on ``kind``. Narrowing on it gives an applier exactly the
#: fields that variant carries, so ``mutation["from"]`` after a
#: ``kind == "copy"`` check needs no ``.get`` guard and no assertion.
type Mutation = CopyMutation | RemoveMutation | SetMutation


class ExpectedFailure(TypedDict):
    """What the mutated document must be rejected for.

    ``stage`` is the ownership boundary, not decoration. ``structural`` cases
    are Ajv's vocabulary — its error keywords and JSON pointers — and belong
    to the site's validator. Every other stage is expressed in the runtime's
    own vocabulary and belongs to an implementation's conformance suite. A
    Python suite that tried to reproduce Ajv's ``code``/``params`` output
    would be testing a reimplementation of Ajv, not the contract.

    ``params`` appears on 23 of the 95 published cases (structural ones that
    name the offending property); the other stages carry their detail in
    ``code`` alone.
    """

    stage: str
    code: str
    path: str
    params: NotRequired[Mapping[str, Any]]


class InvalidCase(TypedDict):
    """One table-driven negative case: a positive fixture, edits, a verdict."""

    id: str
    base: str
    mutations: Sequence[Mutation]
    expected: ExpectedFailure


#: Every directory that has to be present for a corpus to count as complete.
#: Checked in full rather than by sampling one of them: a partial artifact set
#: satisfies imports and then returns empty tuples from the accessors, which
#: is the silent-pass failure this module exists to refuse.
_REQUIRED_ROOTS: tuple[str, ...] = (
    "schemas",
    "examples/paa-tasks",
    "examples/runtime-conformance",
)


def _missing_roots(root: Path) -> tuple[str, ...]:
    return tuple(name for name in _REQUIRED_ROOTS if not (root / name).is_dir())


def _resolve_data_root() -> tuple[Path, Literal["packaged", "worktree"]]:
    """Locate a *complete* set of contract artifacts, packaged or in a checkout.

    Two modes, deliberately, because there are two ways this package is
    legitimately used. An installed wheel carries the artifacts under
    ``_data/``, placed there by hatch_build.py. An editable install from the
    site repo does not — hatchling's editable path hook exposes the source
    tree, and the hook has nothing to copy into. Falling back to the working
    tree makes ``uv pip install -e packages/paa-contracts`` behave identically
    to a real install, which keeps the site→runtime dev loop from needing a
    release for every schema edit.

    Both modes resolve to the same bytes from the same commit, so which one is
    active never changes an answer — but it changes what a failure means,
    which is why it is reported rather than hidden.

    Completeness is required of both, and that is the part worth stating.
    Accepting a root because one expected directory is present would let a
    wheel missing a fixture tree import cleanly as ``packaged``, after which
    every accessor for the missing tree returns ``()`` and a conformance suite
    iterates nothing and reports success. The build-time check in
    scripts/verify_built_wheel.py cannot help a consumer who installs such a
    wheel; this can.
    """
    packaged = Path(__file__).resolve().parent / "_data"
    if packaged.is_dir() and not _missing_roots(packaged):
        return packaged, "packaged"

    for parent in Path(__file__).resolve().parents:
        if not _missing_roots(parent):
            return parent, "worktree"

    detail = (
        f"the packaged _data/ at {packaged} is missing {list(_missing_roots(packaged))}"
        if packaged.is_dir()
        else f"there is no packaged _data/ at {packaged}"
    )
    raise ContractsUnavailableError(
        f"paa-contracts could not locate a complete set of contract artifacts: "
        f"{detail}, and no site checkout above it carries all of "
        f"{list(_REQUIRED_ROOTS)}. A wheel must be built from a full paa.dev "
        "checkout so hatch_build.py has schemas/ and examples/ to pull from."
    )


CONTRACTS_ROOT, DATA_SOURCE = _resolve_data_root()

SCHEMAS_ROOT: Path = CONTRACTS_ROOT / "schemas"
EXAMPLES_ROOT: Path = CONTRACTS_ROOT / "examples"
TASK_FIXTURES_ROOT: Path = EXAMPLES_ROOT / "paa-tasks"
RUNTIME_FIXTURES_ROOT: Path = EXAMPLES_ROOT / "runtime-conformance"

#: A byte-mismatched artifact filed under another record's content address.
#: It lives outside the positive corpus so the discovery walks never pick it
#: up, and exists only to give a tamper check something real to fail on.
TAMPERED_EVIDENCE_ROOT: Path = (
    RUNTIME_FIXTURES_ROOT / "invalid" / "fixtures" / "tampered-evidence"
)

_CASE_TABLES: Mapping[CaseKind, Path] = {
    "task": TASK_FIXTURES_ROOT / "invalid" / "cases.json",
    "evidence": RUNTIME_FIXTURES_ROOT / "invalid" / "evidence-cases.json",
    "decision": RUNTIME_FIXTURES_ROOT / "invalid" / "decision-cases.json",
    "event": RUNTIME_FIXTURES_ROOT / "invalid" / "event-cases.json",
}

#: Where each kind's ``base`` reference resolves from. Task and event bases are
#: plain filenames; evidence and decision bases are ``evidence/paa/<sha>/…``
#: refs relative to their content-addressed root.
_CASE_BASE_ROOTS: Mapping[CaseKind, Path] = {
    "task": TASK_FIXTURES_ROOT,
    "evidence": RUNTIME_FIXTURES_ROOT / "evidence-records",
    "decision": RUNTIME_FIXTURES_ROOT / "decision-artifacts",
    "event": RUNTIME_FIXTURES_ROOT / "autonomy-events",
}


def schema_path(schema_id: SchemaId) -> Path:
    """The file path of one normative schema."""
    if schema_id not in SCHEMA_IDS:
        raise KeyError(f"unknown contract {schema_id!r}; known: {list(SCHEMA_IDS)}")
    return SCHEMAS_ROOT / f"{schema_id}.schema.json"


def load_schema(schema_id: SchemaId) -> dict[str, Any]:
    """One normative schema, parsed."""
    data: dict[str, Any] = json.loads(schema_path(schema_id).read_text(encoding="utf-8"))
    return data


def schema_version(schema_id: SchemaId) -> str:
    """The declared ``x-paa-schema-version`` of one contract.

    Read from the schema rather than mirrored into a constant here. Package
    version and schema-family versions drift independently on purpose — a
    packaging fix should not imply a contract revision — so the only honest
    source for a family version is the schema file that declares it.
    """
    version = load_schema(schema_id).get("x-paa-schema-version")
    if not isinstance(version, str):
        raise ContractsUnavailableError(
            f"{schema_id}.schema.json has no string x-paa-schema-version"
        )
    return version


def _sorted_files(directory: Path, suffix: str) -> tuple[Path, ...]:
    return tuple(sorted(p for p in directory.glob(f"*{suffix}") if p.is_file()))


def _content_addressed_files(directory: Path) -> tuple[Path, ...]:
    paa_dir = directory / "evidence" / "paa"
    if not paa_dir.is_dir():
        return ()
    return tuple(
        sorted(
            path
            for sha_dir in paa_dir.iterdir()
            if (path := sha_dir / "evidence.json").is_file()
        )
    )


def task_declaration_paths() -> tuple[Path, ...]:
    """The valid task-declaration YAML fixtures."""
    return _sorted_files(TASK_FIXTURES_ROOT, ".yaml")


def autonomy_event_paths() -> tuple[Path, ...]:
    """The valid autonomy-event sequences — one motion's full history per file."""
    return _sorted_files(RUNTIME_FIXTURES_ROOT / "autonomy-events", ".json")


def evidence_record_paths() -> tuple[Path, ...]:
    """The valid content-addressed evidence records."""
    return _content_addressed_files(RUNTIME_FIXTURES_ROOT / "evidence-records")


def decision_artifact_paths() -> tuple[Path, ...]:
    """The valid content-addressed decision artifacts."""
    return _content_addressed_files(RUNTIME_FIXTURES_ROOT / "decision-artifacts")


def payload_schema_paths() -> tuple[Path, ...]:
    """Task-specific companion schemas that ``allOf``-narrow the evidence envelope."""
    return _sorted_files(RUNTIME_FIXTURES_ROOT / "payload-schemas", ".schema.json")


def invalid_cases(kind: CaseKind, *, stage: str | None = None) -> tuple[InvalidCase, ...]:
    """The invalid-case table for one contract, optionally filtered by stage.

    Pass ``stage`` to take only the cases whose vocabulary you own. A Python
    conformance suite wants the semantic stages (``semantic``, ``pinned``,
    ``evidence_semantic``, ``decision_semantic``, ``event_semantic``) and
    should leave ``structural`` to the site's Ajv validator — see
    ``ExpectedFailure`` for why that split is the contract's, not a
    convenience.

    A ``stage`` this table does not contain raises rather than filtering to
    nothing. Returning ``()`` would mean ``stage="event_semantics"`` — one
    character off — runs zero cases and passes, and a conformance suite that
    silently checks nothing is worse than one that does not exist, because it
    reports a green result. Pinning the vocabulary in a test constrains what
    this package publishes; it does not constrain what a caller types, and
    the caller is where the typo happens.
    """
    if kind not in _CASE_TABLES:
        raise KeyError(f"unknown case kind {kind!r}; known: {list(CASE_KINDS)}")
    cases: list[InvalidCase] = json.loads(_CASE_TABLES[kind].read_text(encoding="utf-8"))
    if stage is None:
        return tuple(cases)
    present = sorted({case["expected"]["stage"] for case in cases})
    if stage not in present:
        raise KeyError(
            f"no {kind!r} cases carry stage {stage!r}; this table has {present}"
        )
    return tuple(case for case in cases if case["expected"]["stage"] == stage)


def case_stages(kind: CaseKind) -> tuple[str, ...]:
    """The distinct expected stages present in one case table, sorted."""
    return tuple(sorted({case["expected"]["stage"] for case in invalid_cases(kind)}))


def resolve_case_base(kind: CaseKind, case: InvalidCase) -> Path:
    """The positive fixture an invalid case mutates.

    Each kind expresses ``base`` in its own terms — task and event cases name
    a file, evidence and decision cases carry an ``evidence/paa/<sha>/…``
    content-address ref — so resolving it is kind-dependent. Doing it here
    once keeps every consumer from reinventing the mapping and getting the
    evidence-vs-decision root backwards, which would silently mutate the
    wrong artifact and still fail, for the wrong reason.
    """
    base = _CASE_BASE_ROOTS[kind] / case["base"]
    if not base.is_file():
        raise ContractsUnavailableError(
            f"invalid case {case['id']!r} names base {case['base']!r}, "
            f"which does not resolve to a file under {_CASE_BASE_ROOTS[kind]}"
        )
    return base


__all__ = [
    "CASE_KINDS",
    "CONTRACTS_ROOT",
    "DATA_SOURCE",
    "EXAMPLES_ROOT",
    "RUNTIME_FIXTURES_ROOT",
    "SCHEMAS_ROOT",
    "SCHEMA_IDS",
    "TAMPERED_EVIDENCE_ROOT",
    "TASK_FIXTURES_ROOT",
    "CaseKind",
    "ContractsUnavailableError",
    "CopyMutation",
    "ExpectedFailure",
    "InvalidCase",
    "Mutation",
    "RemoveMutation",
    "SchemaId",
    "SetMutation",
    "__version__",
    "autonomy_event_paths",
    "case_stages",
    "decision_artifact_paths",
    "evidence_record_paths",
    "invalid_cases",
    "load_schema",
    "payload_schema_paths",
    "resolve_case_base",
    "schema_path",
    "schema_version",
    "task_declaration_paths",
]
