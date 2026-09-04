"""Reading the published corpus the way its own case tables describe it.

Shared by the corpus-integrity suite. Kept out of the test module so the
machinery for *getting at* a case — loading its base, applying its
mutations, flattening a validator's errors — stays separate from the
claims made about it, and can be tested on its own.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import paa_contracts as contracts
import yaml
from jsonschema import Draft202012Validator, FormatChecker

from conformance._mutations import apply_mutations

#: Which schema governs each case table's documents.
SCHEMA_FOR_KIND: dict[str, str] = {
    "operating": "paa-operating-record",
    "task": "paa-task",
    "evidence": "paa-evidence-record",
    "decision": "paa-decision-artifact",
    "event": "paa-autonomy-event",
}

#: Case kinds whose fixtures are a *sequence* of documents rather than
#: one. An autonomy event fixture is a motion's whole history, and the
#: schema describes a single event, so each element is validated on its
#: own — which is why these cases mutate ``/0/actor`` and expect the
#: error at ``/actor``.
SEQUENCE_KINDS: frozenset[str] = frozenset({"event"})

# Built once. A FormatChecker with no format validators installed is a
# silent no-op, so this is asserted rather than assumed — see
# TestFormatAssertionIsLive, and the pyproject note on format-nongpl.
_FORMAT_CHECKER = FormatChecker()


@dataclass(frozen=True, slots=True)
class SchemaViolation:
    """One validator error, reduced to what the contract's tables assert.

    ``code`` is the JSON Schema keyword that failed and ``pointer`` the
    RFC 6901 path to the instance member it failed on — the two fields a
    published case names. Deliberately not carrying ``params``: it is the
    one part of a validator error whose shape is the validator's own
    rather than the specification's, no published case asserts it, and
    carrying it would invite a case that does.
    """

    code: str
    pointer: str


def load_document(path: Path) -> Any:
    """A corpus fixture, whichever of the two published forms it takes."""
    text = path.read_text(encoding="utf-8")
    return yaml.safe_load(text) if path.suffix in (".yaml", ".yml") else json.loads(text)


def case_documents(kind: str, case: contracts.InvalidCase) -> tuple[Any, Any]:
    """The ``(base, mutated)`` pair a case describes.

    Both, always. Every claim about a mutated document is worth exactly
    what the claim that its base was fine is worth: a case whose base
    already violated the rule under test would pass its assertion while
    proving nothing about the mutation.
    """
    base = load_document(contracts.resolve_case_base(kind, case))
    return base, apply_mutations(base, case["mutations"])


def violations(kind: str, document: Any) -> tuple[SchemaViolation, ...]:
    """Every schema violation in *document*, flattened.

    Nested errors are walked rather than reported at the top level.
    ``oneOf`` and ``anyOf`` report a single failure at the branch point
    and hang the real reasons off ``context``; the published cases name
    those reasons, so a suite that read only the top level would miss
    them — five of the task table's cases, specifically.
    """
    validator = Draft202012Validator(
        contracts.load_schema(SCHEMA_FOR_KIND[kind]), format_checker=_FORMAT_CHECKER,
    )
    documents = document if kind in SEQUENCE_KINDS and isinstance(document, list) else [document]

    def walk(errors: Any) -> Iterator[SchemaViolation]:
        for error in errors:
            yield SchemaViolation(
                code=error.validator,
                pointer="".join(
                    "/" + str(part).replace("~", "~0").replace("/", "~1")
                    for part in error.absolute_path
                ),
            )
            yield from walk(error.context or [])

    return tuple(v for doc in documents for v in walk(validator.iter_errors(doc)))


def expected_violation(case: contracts.InvalidCase) -> SchemaViolation:
    return SchemaViolation(code=case["expected"]["code"], pointer=case["expected"]["path"])
