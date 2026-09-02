"""Replay a history captured from a consumer's pre-cutover implementation."""

from __future__ import annotations

import json
from typing import Any

import paa_contracts as contracts
from jsonschema import Draft202012Validator, FormatChecker

from paa_runtime import RuntimeConfig, SqliteEventStore, list_motions, show
from paa_runtime.replay import import_events


def test_legacy_pre_cutover_capture_reproduces_its_projections(
    runtime_config: RuntimeConfig,
) -> None:
    archive_path = contracts.EXAMPLES_ROOT / "legacy-archive" / "pre-cutover-capture.json"
    archive: dict[str, Any] = json.loads(archive_path.read_text(encoding="utf-8"))

    assert archive["capture_kind"] == "pre-cutover-implementation"
    assert archive["production_event_count_at_cutover"] == 0

    validator = Draft202012Validator(
        contracts.load_schema("paa-autonomy-event"), format_checker=FormatChecker(),
    )
    for event in archive["events"]:
        validator.validate(event)

    store = SqliteEventStore(runtime_config.db_path)
    try:
        import_events(store, archive["events"])

        stored = [event.to_json_dict() for event in store.get_autonomy_events()]
        assert stored == archive["events"]

        positions = []
        for expected in archive["positions"]:
            actual = show(
                store, runtime_config,
                task=expected["task"], scope=expected["scope"],
            )
            latest = actual.pop("latest_position_event")
            actual["latest_position_event_id"] = latest["id"] if latest else None
            positions.append(actual)
        assert positions == archive["positions"]

        assert [motion.to_json_dict() for motion in list_motions(store)] == archive["motions"]
    finally:
        store.close()
