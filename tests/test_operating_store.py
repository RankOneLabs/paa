"""Operating accounting is append-only, lossless, and independent of authority."""

from __future__ import annotations

import copy
import json
import sqlite3
from pathlib import Path
from typing import Literal

import pytest

from paa_runtime.operating import OperatingRecord, OperatingRecordError, decode_operating_record
from paa_runtime.operating_store import OperatingStoreError, SqliteOperatingRecordStore


@pytest.fixture
def record() -> OperatingRecord:
    """Minimal illustrative task attempt, shaped by the operating contract."""
    return {
        "record_schema": "paa-operating-record/0.1.0-draft",
        "record_id": "attempt-1", "task": "reply_draft", "declaration_version": 1,
        "scope": None, "subject": {"kind": "run", "id": "run-1"},
        "worker": {"id": "drafter", "version": "v1", "configuration_ref": "cfg:1"},
        "usage": {"input_tokens": 100, "output_tokens": None},
        "price": {"currency": "USD", "amount": 0.001, "basis": "catalog:v1"},
        "timestamps": {
            "started_at": "2026-01-01T00:00:00Z", "completed_at": "2026-01-01T00:00:01Z",
            "recorded_at": "2026-01-01T00:00:02Z",
        },
        "source_references": ["app://usage/run-1/attempt-1"],
    }


def test_record_survives_reopening(tmp_path: Path, record: OperatingRecord) -> None:
    path = tmp_path / "records.db"
    store = SqliteOperatingRecordStore(path)
    store.append(record)
    store.close()
    reopened = SqliteOperatingRecordStore(path)
    try:
        assert reopened.get_by_subject(record["subject"]) == (record,)
    finally:
        reopened.close()


def test_attempts_share_subject_without_overwriting(
    tmp_path: Path, record: OperatingRecord,
) -> None:
    retry = copy.deepcopy(record)
    retry.update(record_id="attempt-2", price=None, usage=None)
    retry["source_references"] = ["app://usage/run-1/failed-attempt-2"]
    store = SqliteOperatingRecordStore(tmp_path / "records.db")
    try:
        store.append(record)
        store.append(retry)
        assert store.get_by_subject(record["subject"]) == (record, retry)
    finally:
        store.close()


def test_subject_lookup_matches_kind_and_id(tmp_path: Path, record: OperatingRecord) -> None:
    store = SqliteOperatingRecordStore(tmp_path / "records.db")
    try:
        store.append(record)
        assert store.get_by_subject({"kind": "case", "id": "run-1"}) == ()
    finally:
        store.close()


def test_subject_lookup_excludes_other_ids(tmp_path: Path, record: OperatingRecord) -> None:
    store = SqliteOperatingRecordStore(tmp_path / "records.db")
    try:
        store.append(record)
        assert store.get_by_subject({"kind": "run", "id": "run-2"}) == ()
    finally:
        store.close()


def test_same_subject_retains_task_scope_and_configuration(
    tmp_path: Path, record: OperatingRecord,
) -> None:
    other = copy.deepcopy(record)
    other.update(record_id="other-task", task="reply_review", scope="review:public")
    other["worker"]["configuration_ref"] = "cfg:review"
    store = SqliteOperatingRecordStore(tmp_path / "records.db")
    try:
        store.append(record)
        store.append(other)
        assert store.get_by_subject(record["subject"]) == (record, other)
    finally:
        store.close()


def test_reused_record_id_fails_and_original_remains(
    tmp_path: Path, record: OperatingRecord,
) -> None:
    store = SqliteOperatingRecordStore(tmp_path / "records.db")
    try:
        store.append(record)
        with pytest.raises(OperatingStoreError, match="append operating record 'attempt-1'"):
            store.append(record)
        assert store.get_by_subject(record["subject"]) == (record,)
    finally:
        store.close()


@pytest.mark.parametrize("statement", [
    "UPDATE operating_records SET document = '{}'",
    "DELETE FROM operating_records",
    "INSERT OR REPLACE INTO operating_records "
    "(record_id, subject_kind, subject_id, document) VALUES ('attempt-1', 'run', 'r', '{}')",
    "INSERT OR REPLACE INTO operating_records "
    "(sequence, record_id, subject_kind, subject_id, document) "
    "VALUES (1, 'other', 'run', 'r', '{}')",
])
def test_storage_rejects_mutation_from_another_connection(
    tmp_path: Path, record: OperatingRecord, statement: str,
) -> None:
    path = tmp_path / "records.db"
    store = SqliteOperatingRecordStore(path)
    store.append(record)
    connection = sqlite3.connect(path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(statement)
        connection.rollback()
        assert store.get_by_subject(record["subject"]) == (record,)
    finally:
        connection.close()
        store.close()


def test_caller_mutation_cannot_change_stored_record(
    tmp_path: Path, record: OperatingRecord,
) -> None:
    expected = copy.deepcopy(record)
    store = SqliteOperatingRecordStore(tmp_path / "records.db")
    try:
        store.append(record)
        record["worker"]["configuration_ref"] = "cfg:changed"
        read = store.get_by_subject(record["subject"])
        read[0]["source_references"].append("new-reference")
        assert store.get_by_subject(record["subject"]) == (expected,)
    finally:
        store.close()


def test_invalid_record_is_not_partially_written(tmp_path: Path, record: OperatingRecord) -> None:
    record["worker"]["configuration_ref"] = ""
    store = SqliteOperatingRecordStore(tmp_path / "records.db")
    try:
        with pytest.raises(OperatingRecordError, match="/worker/configuration_ref"):
            store.append(record)
        assert store.get_by_subject(record["subject"]) == ()
    finally:
        store.close()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_non_json_numbers_are_rejected(record: OperatingRecord, value: float) -> None:
    record["usage"] = {"tokens": value}
    with pytest.raises(OperatingRecordError, match="finite"):
        decode_operating_record(json.dumps(record).encode())


def test_malformed_json_fails_closed() -> None:
    with pytest.raises(OperatingRecordError, match="decode operating record"):
        decode_operating_record(b"not-json")


@pytest.mark.parametrize("stamp", [
    "2026-01-01T00:00:00+01:99", "2026-01-01T24:00:00Z", "2026-02-30T00:00:00Z",
])
@pytest.mark.parametrize("field", ["started_at", "completed_at", "recorded_at"])
def test_invalid_timestamp_reports_its_field_path(
    record: OperatingRecord, stamp: str,
    field: Literal["started_at", "completed_at", "recorded_at"],
) -> None:
    record["timestamps"][field] = stamp
    with pytest.raises(OperatingRecordError, match=f"/timestamps/{field}"):
        decode_operating_record(json.dumps(record).encode())


def test_database_open_failure_has_operation_context(tmp_path: Path) -> None:
    with pytest.raises(OperatingStoreError, match="open operating store"):
        SqliteOperatingRecordStore(tmp_path / "missing-directory" / "records.db")


def test_multiple_connections_observe_committed_records(
    tmp_path: Path, record: OperatingRecord,
) -> None:
    path = tmp_path / "records.db"
    first = SqliteOperatingRecordStore(path)
    second = SqliteOperatingRecordStore(path)
    try:
        first.append(record)
        assert second.get_by_subject(record["subject"]) == (record,)
    finally:
        second.close()
        first.close()
