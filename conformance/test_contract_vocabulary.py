"""The runtime's vocabulary against the published schemas' own.

The runtime hardcodes small closed sets — positions, event types, the
event_schema stamp, deployment and window and execution values — because
it has to decide things about them at runtime, and reading a JSON schema
to do that would put a parser on the hot path of a governed effect. The
cost of that choice is drift: a contract can add a value and nothing in
this repo notices.

These are the assertions that make the README's claim ("tracking the
paa-task/0.2.1-draft and paa-autonomy-event/0.1.0-draft schema families")
checkable rather than a sentence someone has to remember to update.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import paa_contracts as contracts

import paa_runtime.declarations as declarations
from paa_runtime import AutonomyEvent
from paa_runtime.events import (
    AUTONOMY_EVENT_TYPES,
    AUTONOMY_POSITIONS,
    CURRENT_EVENT_SCHEMA,
)

TASK_SCHEMA: dict[str, Any] = contracts.load_schema("paa-task")
EVENT_SCHEMA: dict[str, Any] = contracts.load_schema("paa-autonomy-event")


def _task_definition_enum(name: str) -> set[str]:
    return set(TASK_SCHEMA["definitions"][name]["enum"])


class TestSchemaFamilies:
    """The families this release targets are the ones it is checked against."""

    def test_event_schema_stamp_matches_the_published_family(self) -> None:
        published = contracts.schema_version("paa-autonomy-event")
        assert published == CURRENT_EVENT_SCHEMA

    def test_event_schema_stamp_matches_the_const_the_schema_requires(self) -> None:
        # Every event record must carry this exact string, so a stamp that
        # drifts from the const makes every event the runtime writes
        # invalid against the contract it claims to implement.
        required = EVENT_SCHEMA["properties"]["event_schema"]["const"]
        assert required == CURRENT_EVENT_SCHEMA

    def test_task_family_is_the_one_the_readme_states(self) -> None:
        assert contracts.schema_version("paa-task") == "paa-task/0.2.1-draft"


class TestVocabularyParity:
    """Each closed set the runtime hardcodes equals the schema's.

    Published value on the left throughout: the contract states the
    vocabulary, and this implementation is what is being measured
    against it.
    """

    def test_autonomy_positions(self) -> None:
        published = _task_definition_enum("autonomy_position")
        assert published == AUTONOMY_POSITIONS

    def test_positions_agree_across_both_schemas(self) -> None:
        # paa-task and paa-autonomy-event each define the position
        # vocabulary; the runtime has one set for both.
        published = set(EVENT_SCHEMA["definitions"]["autonomy_position"]["enum"])
        assert published == AUTONOMY_POSITIONS

    def test_autonomy_event_types(self) -> None:
        published = set(EVENT_SCHEMA["properties"]["event"]["enum"])
        assert published == AUTONOMY_EVENT_TYPES

    def test_deployments(self) -> None:
        published = _task_definition_enum("deployment")
        assert published == declarations._DEPLOYMENTS

    def test_position_policy_modes(self) -> None:
        published = _task_definition_enum("position_policy_value")
        assert published == declarations._POSITION_POLICY_MODES

    def test_promotion_executions(self) -> None:
        published = set(
            TASK_SCHEMA["properties"]["promotion"]["properties"]["execution"]["enum"]
        )
        assert published == declarations._PROMOTION_EXECUTIONS

    def test_window_kinds(self) -> None:
        # The window is a oneOf over a cases branch and a duration branch,
        # each pinning its own single-valued kind enum.
        published = {
            kind
            for branch in TASK_SCHEMA["definitions"]["window"]["oneOf"]
            for kind in branch["properties"]["kind"]["enum"]
        }
        assert published == declarations._WINDOW_KINDS


class TestEventRowShape:
    """The row type carries exactly the fields the contract requires."""

    def test_field_order_matches_the_schemas_required_array(self) -> None:
        # AutonomyEvent's docstring claims this ordering; asserting it is
        # what lets the round-trip check compare whole documents rather
        # than a hand-maintained field list.
        assert [f.name for f in dataclasses.fields(AutonomyEvent)] == EVENT_SCHEMA["required"]

    def test_json_dict_keys_match_the_schemas_required_array(self) -> None:
        event = AutonomyEvent(
            event_schema=CURRENT_EVENT_SCHEMA,
            id="e", motion_id="m", task="t", declaration_version=1, scope=None,
            event="motion_proposed", from_position="hitl", to_position="hotl",
            evidence_ref="evidence/paa/" + "0" * 64 + "/evidence.json",
            evidence_sha256="0" * 64, actor="operator:test", reason="r",
            created_at="2026-04-01T08:00:00.000000Z",
        )
        assert list(event.to_json_dict()) == EVENT_SCHEMA["required"]

    def test_the_schema_admits_no_fields_beyond_those(self) -> None:
        # additionalProperties:false is what makes "carries exactly" a
        # two-sided claim rather than "carries at least".
        assert EVENT_SCHEMA["additionalProperties"] is False
        assert set(EVENT_SCHEMA["properties"]) == set(EVENT_SCHEMA["required"])
