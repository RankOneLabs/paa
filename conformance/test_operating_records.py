"""Published operating records round-trip without becoming policy inputs."""

from __future__ import annotations

import json
from pathlib import Path

import paa_contracts as contracts
import pytest
from jsonschema import Draft7Validator

from conformance._corpus import case_documents, violations
from paa_runtime import RuntimeConfig, SqliteEventStore, approve, propose, show
from paa_runtime.operating import (
    CURRENT_OPERATING_SCHEMA,
    OperatingRecordError,
    decode_operating_record,
)
from paa_runtime.operating_store import SqliteOperatingRecordStore


def test_operating_schema_stamp_matches_contract() -> None:
    assert contracts.schema_version("paa-operating-record") == CURRENT_OPERATING_SCHEMA


@pytest.mark.parametrize("schema_id", ["paa-operating-record", "paa-evidence-record"])
def test_revised_contract_is_a_valid_draft7_schema(schema_id: contracts.SchemaId) -> None:
    Draft7Validator.check_schema(contracts.load_schema(schema_id))


def test_worker_definitions_agree() -> None:
    operating = contracts.load_schema("paa-operating-record")["definitions"]["worker"]
    evidence = dict(contracts.load_schema("paa-evidence-record")["definitions"]["worker"])
    evidence.pop("description")
    assert operating == evidence


@pytest.mark.parametrize("path", contracts.operating_record_paths(), ids=lambda path: path.name)
def test_operating_fixture_round_trips(path: Path, tmp_path: Path) -> None:
    record = decode_operating_record(path.read_bytes())
    store = SqliteOperatingRecordStore(tmp_path / "records.db")
    try:
        store.append(record)
        assert store.get_by_subject(record["subject"]) == (json.loads(path.read_bytes()),)
    finally:
        store.close()


@pytest.mark.parametrize("case", contracts.invalid_cases("operating"), ids=lambda case: case["id"])
def test_decoder_rejects_published_invalid_case(case: contracts.InvalidCase) -> None:
    _, mutated = case_documents("operating", case)
    with pytest.raises(OperatingRecordError):
        decode_operating_record(json.dumps(mutated).encode())


def test_current_evidence_without_worker_remains_valid() -> None:
    record = json.loads(contracts.evidence_record_paths()[0].read_bytes())
    record["record_schema"] = "paa-evidence-record/0.2.0-draft"
    record.pop("worker", None)
    assert violations("evidence", record) == ()


def test_operating_records_do_not_change_motion_outcomes(
    runtime_config: RuntimeConfig, tmp_path: Path,
) -> None:
    """Same database, deliberately changed cost/worker; approval still independent."""
    events = SqliteEventStore(runtime_config.db_path)
    operating = SqliteOperatingRecordStore(runtime_config.db_path)
    evidence = tmp_path / "report.json"
    evidence.write_bytes(b'{"operator_report": "illustrative"}')
    try:
        motion = propose(
            events, runtime_config, task="outbound_content_publish", scope="publish:farcaster",
            to_position="hotl", evidence_path=evidence, actor="test:operator",
        )
        before = show(
            events, runtime_config, task="outbound_content_publish", scope="publish:farcaster",
        )
        for index, path in enumerate(contracts.operating_record_paths()):
            record = decode_operating_record(path.read_bytes())
            record.update(task="outbound_content_publish", scope="publish:farcaster")
            record["worker"]["configuration_ref"] = f"cfg:changed-{index}"
            operating.append(record)
        assert show(
            events, runtime_config, task="outbound_content_publish", scope="publish:farcaster",
        ) == before
        approve(
            events, runtime_config, motion_id=motion.motion_id,
            actor="test:operator", reason="independent operator approval",
        )
        assert events.get_autonomy_events()[-1].to_position == "hotl"
    finally:
        operating.close()
        events.close()
