"""Independent append-only operating records; no dependency on authority state.

The SQLite implementation may use the event store's database path or a separate
database. It does not alter EventStore, evidence files, or the motion service.
One append is one transaction. Record IDs are unique; subjects are not, since
attempts and summaries may share a subject without representing new outcomes.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Protocol

from paa_runtime.operating import OperatingRecord, RecordSubject, decode_operating_record


class OperatingStoreError(RuntimeError):
    """Storage failure with operation and record/subject context."""


class OperatingRecordStore(Protocol):
    """Append records and retrieve all records for an exact subject.

    Append rejects reused record IDs, including identical duplicates. No
    update/delete API exists and mutation must be blocked in storage.
    Retrieval returns insertion order and preserves all fields, including
    source references, missing measurements, and optional components.
    Consumers must reconcile overlapping records before aggregating costs.
    """

    def append(self, record: OperatingRecord) -> None:
        """Atomically append one structurally valid record."""
        ...

    def get_by_subject(self, subject: RecordSubject) -> tuple[OperatingRecord, ...]:
        """All records with this exact subject kind and ID, in insertion order."""
        ...


_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS operating_records (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id TEXT NOT NULL UNIQUE,
    subject_kind TEXT NOT NULL CHECK(subject_kind IN ('case', 'run')),
    subject_id TEXT NOT NULL,
    document TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS operating_records_subject_idx
    ON operating_records(subject_kind, subject_id, sequence);
CREATE TRIGGER IF NOT EXISTS operating_records_no_replace
BEFORE INSERT ON operating_records
WHEN EXISTS (
    SELECT 1 FROM operating_records
    WHERE record_id = NEW.record_id OR sequence = NEW.sequence
)
BEGIN
    SELECT RAISE(ABORT, 'operating_records record already exists');
END;
CREATE TRIGGER IF NOT EXISTS operating_records_no_update
BEFORE UPDATE ON operating_records
BEGIN
    SELECT RAISE(ABORT, 'operating_records is append-only');
END;
CREATE TRIGGER IF NOT EXISTS operating_records_no_delete
BEFORE DELETE ON operating_records
BEGIN
    SELECT RAISE(ABORT, 'operating_records is append-only');
END;
"""


class SqliteOperatingRecordStore:
    """Owns a connection and a sibling table, never an autonomy event stream."""

    def __init__(self, db_path: Path) -> None:
        try:
            self._conn = sqlite3.connect(db_path)
        except sqlite3.Error as error:
            raise OperatingStoreError(f"open operating store {db_path}: {error}") from error
        try:
            # Enables deletion triggers for REPLACE as well as explicit DELETE.
            self._conn.execute("PRAGMA recursive_triggers=ON")
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(_SCHEMA_DDL)
            self._conn.commit()
        except sqlite3.Error as error:
            self._conn.close()
            raise OperatingStoreError(f"initialize operating store {db_path}: {error}") from error

    def close(self) -> None:
        """Release this store's connection."""
        self._conn.close()

    def append(self, record: OperatingRecord) -> None:
        """Validate at IO, then insert atomically; duplicate IDs fail closed."""
        try:
            document = json.dumps(record, allow_nan=False)
        except (ValueError, TypeError, RecursionError) as error:
            raise OperatingStoreError(f"serialize operating record: {error}") from error
        checked = decode_operating_record(document.encode("utf-8"))
        try:
            with self._conn:
                self._conn.execute(
                    """INSERT INTO operating_records
                       (record_id, subject_kind, subject_id, document) VALUES (?, ?, ?, ?)""",
                    (checked["record_id"], checked["subject"]["kind"],
                     checked["subject"]["id"], document),
                )
        except sqlite3.Error as error:
            raise OperatingStoreError(
                f"append operating record {checked['record_id']!r}: {error}"
            ) from error

    def get_by_subject(self, subject: RecordSubject) -> tuple[OperatingRecord, ...]:
        """Return detached, validated records; no costing or policy decisions."""
        try:
            rows = self._conn.execute(
                """SELECT document FROM operating_records
                   WHERE subject_kind = ? AND subject_id = ? ORDER BY sequence""",
                (subject["kind"], subject["id"]),
            ).fetchall()
        except sqlite3.Error as error:
            raise OperatingStoreError(f"read operating subject {subject!r}: {error}") from error
        return tuple(decode_operating_record(row[0].encode("utf-8")) for row in rows)


__all__ = ["OperatingRecordStore", "SqliteOperatingRecordStore", "OperatingStoreError"]
