# paa-runtime

The executable side of the [Progressive Autonomy Architecture](https://www.paa.dev): task declarations, the motion lifecycle, position resolution, content-addressed evidence, and an append-only autonomy event store.

PAA is implementation-neutral by construction — it describes *what* a governed autonomy transition must record, not *how*. This package is one implementation of that contract, not the contract itself.

## What it governs

- **Task declarations** — load and validate YAML declarations against the `paa-task` contract, including evaluator identities, position policy, and the declared promotion/demotion transitions.
- **The motion lifecycle** — `propose` → `approve` / `reject`, plus one-command emergency `demote`. Approval and its position change commit atomically or not at all.
- **Position resolution** — current autonomy position is never stored. It is folded fresh from the declaration's `initial_position` plus the latest exact-scope `position_changed` event.
- **Evidence binding** — every motion binds to the exact bytes of its evidence artifact by SHA-256, re-verified at approval. Tamper or loss is a fail-closed error.
- **An append-only event store** — append-only enforced by the storage layer, not by convention.

## What it does not do

- **Produce evaluator verdicts.** The runtime governs; consumers evaluate. Which evaluators exist and what code produces each verdict is consumer domain data, supplied as a registry.
- **Evaluate promotion rules.** Thresholds and windows are *declared*, not machine-evaluated. Approval is an operator judgment.
- **Carry worker identity.** The contract has no worker-identity field yet, so evidence windows cannot prove which worker produced them. Tracked for a later contract cycle.

## Install

```bash
uv add paa-runtime
```

## Use

Adoption is two things: build one `RuntimeConfig`, then call the lifecycle API. The runtime owns its own database — you do not host a table, and you do not implement a protocol.

```python
from pathlib import Path

from paa_runtime import (
    ProducerRegistration,
    RuntimeConfig,
    SqliteEventStore,
    approve,
    propose,
    show,
)

# One entry per evaluator identity your declarations reference. This is the
# consumer domain data the runtime does not own: it says which code produces
# each verdict, and the loader rejects any declaration naming an identity
# that is not registered here.
MY_PRODUCER_REGISTRY = (
    ProducerRegistration(
        property="refund_correctness",
        target="refund_decision",
        technique="rubric_grader",
        oracle="human_labeled",
        version="1.0.0",
        authority="team_finance",
        status="implemented",
    ),
)

config = RuntimeConfig(
    declarations_dir=Path("contracts/paa"),
    evidence_root=Path("."),
    registry=MY_PRODUCER_REGISTRY,
    db_path=Path("paa_runtime.db"),
    actor_env_var="MY_APP_PAA_ACTOR",
)

store = SqliteEventStore(config.db_path)
try:
    motion = propose(
        store, config,
        task="refund_approval",
        scope=None,
        to_position="hotl",
        evidence_path=Path("promotion-report.json"),
        reason="window closed eligible",
    )
    approve(store, config, motion_id=motion.motion_id, reason="reviewed and approved")

    print(show(store, config, task="refund_approval", scope=None))
    # {'task': 'refund_approval', 'current_position': 'hotl', ...}
finally:
    store.close()
```

`scope` is `None` for a task whose declaration has no `scopes` block, and must be one of the declared scopes otherwise.

## Overriding the store

`SqliteEventStore` is the default and most consumers should use it. `EventStore` is a protocol so that a consumer whose governed effect and the position read authorizing it must commit in a *single lock domain* can implement it over its own connection — a runtime-owned database cannot offer that guarantee across process boundaries. It is one insert, four reads, and two transaction context managers.

The trade-off is named rather than hidden. With the default store, a consumer that resolves a position and then performs the effect it authorizes does so across two lock domains: a demotion committed in between is not seen by the effect already in flight. That window is small and the failure is a stale *permit*, not a corrupt history — but it is real, and a consumer for which it is unacceptable implements `EventStore` over the same connection its effect commits on.

## Development

```bash
uv sync
uv run pytest
uv run ruff check src tests
uv run mypy src/paa_runtime
```

## Status

`0.2.0`, tracking the `paa-task/0.2.1-draft` and `paa-autonomy-event/0.1.0-draft` schema families. Package and spec versions drift independently — the schema families a release targets are stated here and asserted by the conformance suite, not inferred from the package version.
