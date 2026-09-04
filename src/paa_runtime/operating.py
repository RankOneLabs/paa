"""Named wire types and an IO decoder for paa-operating-record/0.1.0-draft.

These types mirror schemas/paa-operating-record.schema.json. WorkerIdentity
also mirrors the optional evidence worker definition. Neither is a policy
input: the motion service does not import this module.
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime
from typing import Literal, Never, NotRequired, TypedDict, cast

CURRENT_OPERATING_SCHEMA = "paa-operating-record/0.1.0-draft"


class WorkerIdentity(TypedDict):
    """The worker role, its version, and opaque complete configuration handle."""

    id: str
    version: str
    configuration_ref: str


class RecordSubject(TypedDict):
    """The same subject identity used by evidence records."""

    kind: Literal["case", "run"]
    id: str


class OperatingPrice(TypedDict):
    """A priced amount with a consumer-owned currency and rate/catalog basis."""

    currency: str
    amount: int | float
    basis: str


class OperatingComponent(TypedDict):
    """Additional cost not already included in the record's base price."""

    kind: str
    quantity: int | float | None
    unit: str
    price: OperatingPrice | None


class RecordTimestamps(TypedDict):
    """RFC 3339 timestamps for the attributed work and its recording."""

    started_at: str
    completed_at: str
    recorded_at: str


class OperatingRecord(TypedDict):
    """Wire representation; unavailable measurements are None, not zero.

    Usage keys and component kinds are open consumer vocabulary. Source
    references preserve attribution; this envelope cannot verify external
    provenance, price accuracy, coverage, or overlap between summaries.
    """

    record_schema: Literal["paa-operating-record/0.1.0-draft"]
    record_id: str
    task: str
    declaration_version: int
    scope: str | None
    subject: RecordSubject
    worker: WorkerIdentity
    usage: dict[str, int | float | None] | None
    price: OperatingPrice | None
    timestamps: RecordTimestamps
    source_references: list[str]
    components: NotRequired[list[OperatingComponent]]


class OperatingRecordError(ValueError):
    """Malformed record at the JSON IO boundary, with operation and field path."""


def _fail(path: str, detail: str) -> Never:
    raise OperatingRecordError(f"decode operating record at {path}: {detail}")


def _object(
    value: object, path: str, required: set[str], optional: frozenset[str] = frozenset(),
) -> dict[str, object]:
    # Dynamic JSON is unknown until every member has been validated below.
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        _fail(path, "expected an object")
    result = cast(dict[str, object], value)
    if required - result.keys() or result.keys() - required - optional:
        _fail(path, "missing required or unknown fields")
    return result


def _text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(path, "expected a nonempty string")
    return value


def _quantity(value: object, path: str) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(path, "expected a nonnegative finite number or null")
    if value < 0 or (isinstance(value, float) and not math.isfinite(value)):
        _fail(path, "expected a nonnegative finite number or null")


def _price(value: object, path: str) -> None:
    if value is None:
        return
    price = _object(value, path, {"currency", "amount", "basis"})
    _text(price["currency"], path + "/currency")
    _text(price["basis"], path + "/basis")
    if price["amount"] is None:
        _fail(path + "/amount", "use null price when the amount is unavailable")
    _quantity(price["amount"], path + "/amount")


def _timestamp(value: object, path: str) -> None:
    stamp = _text(value, path)
    pattern = (
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt](?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]"
        r"(?:\.[0-9]+)?(?:[Zz]|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])"
    )
    if re.fullmatch(pattern, stamp) is None:
        _fail(path, "expected an RFC 3339 date-time with timezone")
    try:
        datetime.fromisoformat(stamp.upper().replace("Z", "+00:00"))
    except ValueError as error:
        _fail(path, str(error))


def decode_operating_record(data: bytes) -> OperatingRecord:
    """Decode and structurally check JSON at the store/import boundary.

    No schema package is needed in production. Conformance tests run the
    published positive and negative corpus through both this decoder and
    the JSON Schema. Semantic attribution remains the consumer's duty.
    """
    try:
        return _decode_operating_record(data)
    except OperatingRecordError:
        raise
    except (ValueError, TypeError, OverflowError, RecursionError) as error:
        raise OperatingRecordError(f"decode operating record: {error}") from error


def _decode_operating_record(data: bytes) -> OperatingRecord:
    document: object = json.loads(data)
    record = _object(document, "/", {
        "record_schema", "record_id", "task", "declaration_version", "scope",
        "subject", "worker", "usage", "price", "timestamps", "source_references",
    }, frozenset({"components"}))
    if record["record_schema"] != CURRENT_OPERATING_SCHEMA:
        _fail("/record_schema", "unsupported schema version")
    _text(record["record_id"], "/record_id")
    if re.fullmatch(r"[a-z][a-z0-9_]*", _text(record["task"], "/task")) is None:
        _fail("/task", "invalid task identifier")
    version = record["declaration_version"]
    _quantity(version, "/declaration_version")
    if not isinstance(version, (int, float)) or version < 1 or int(version) != version:
        _fail("/declaration_version", "expected a positive integer")
    record["declaration_version"] = int(version)
    if record["scope"] is not None:
        _text(record["scope"], "/scope")
    subject = _object(record["subject"], "/subject", {"kind", "id"})
    if subject["kind"] not in ("case", "run"):
        _fail("/subject/kind", "expected case or run")
    _text(subject["id"], "/subject/id")
    worker = _object(record["worker"], "/worker", {"id", "version", "configuration_ref"})
    for key, value in worker.items():
        _text(value, "/worker/" + key)
    usage = record["usage"]
    if usage is not None:
        if not isinstance(usage, dict) or not usage:
            _fail("/usage", "expected a nonempty quantity object or null")
        for key, value in cast(dict[str, object], usage).items():
            _text(key, "/usage")
            _quantity(value, "/usage/" + key)
    _price(record["price"], "/price")
    if "components" in record:
        components = record["components"]
        if not isinstance(components, list):
            _fail("/components", "expected an array")
        for index, value in enumerate(cast(list[object], components)):
            path = f"/components/{index}"
            component = _object(value, path, {"kind", "quantity", "unit", "price"})
            _text(component["kind"], path + "/kind")
            _text(component["unit"], path + "/unit")
            _quantity(component["quantity"], path + "/quantity")
            _price(component["price"], path + "/price")
    timestamps = _object(record["timestamps"], "/timestamps", {
        "started_at", "completed_at", "recorded_at",
    })
    for key, value in timestamps.items():
        _timestamp(value, "/timestamps/" + key)
    references = record["source_references"]
    if not isinstance(references, list) or not references:
        _fail("/source_references", "expected a nonempty array")
    strings = [_text(value, "/source_references") for value in cast(list[object], references)]
    if len(set(strings)) != len(strings):
        _fail("/source_references", "duplicate source references")
    # The sole cast to the wire type follows validation of every field.
    return cast(OperatingRecord, record)


__all__ = [
    "CURRENT_OPERATING_SCHEMA", "WorkerIdentity", "RecordSubject", "OperatingPrice",
    "OperatingComponent", "RecordTimestamps", "OperatingRecord", "OperatingRecordError",
    "decode_operating_record",
]
