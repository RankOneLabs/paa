"""Run the refund task through propose, approve, and emergency demotion."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from paa_runtime import (
    PaaEvaluationBasis,
    ProducerRegistration,
    RuntimeConfig,
    SqliteEventStore,
    approve,
    demote,
    propose,
    show,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

REGISTRY = (
    ProducerRegistration(
        property="refund_policy_invariants",
        target="output",
        technique="deterministic",
        evaluation_basis=PaaEvaluationBasis(
            kind="invariant", ref="refund_policy_invariants",
        ),
        epistemic_status="ground_truth",
        version="1",
        authority="blocking",
        status="implemented",
    ),
    ProducerRegistration(
        property="should_escalate",
        target="output",
        technique="classifier",
        evaluation_basis=PaaEvaluationBasis(
            kind="reference_label", ref="should_escalate_reference_label",
        ),
        epistemic_status="ground_truth",
        version="1",
        authority="blocking",
        status="implemented",
    ),
)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="paa-refund-") as raw:
        root = Path(raw)
        declarations = root / "declarations"
        declarations.mkdir()
        shutil.copy(
            REPO_ROOT / "examples" / "paa-tasks" / "refund_approval.v1.yaml",
            declarations,
        )
        report = root / "promotion-report.json"
        report.write_text(
            json.dumps({"eligible_cases": 200, "policy_violations": 0}),
            encoding="utf-8",
        )
        config = RuntimeConfig(
            declarations_dir=declarations,
            evidence_root=root,
            registry=REGISTRY,
            db_path=root / "paa-runtime.db",
            actor_env_var="PAA_QUICKSTART_ACTOR",
        )
        store = SqliteEventStore(config.db_path)
        try:
            proposed = propose(
                store,
                config,
                task="refund_approval",
                scope=None,
                to_position="hotl",
                evidence_path=report,
                actor="quickstart-operator",
                reason="200 eligible cases reviewed",
            )
            approved = approve(
                store,
                config,
                motion_id=proposed.motion_id,
                actor="quickstart-operator",
                reason="promotion evidence accepted",
            )
            at_hotl = show(store, config, task="refund_approval", scope=None)
            demoted = demote(
                store,
                config,
                task="refund_approval",
                scope=None,
                actor="quickstart-operator",
                reason="chargeback threshold crossed",
                source_rows=["refunds:example-201"],
            )
            back_at_hitl = show(store, config, task="refund_approval", scope=None)
        finally:
            store.close()

        print(json.dumps({
            "promotion": approved.to_json_dict(),
            "position_after_promotion": at_hotl,
            "demotion": demoted.to_json_dict(),
            "position_after_demotion": back_at_hitl,
        }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
