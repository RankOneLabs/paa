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
    PaaEvaluationBasis,
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
#
# The identity is the whole evaluator record. evaluation_basis says how the
# verdict is grounded and in what; epistemic_status says whether governance
# treats it as the task's authoritative truth signal or as an approximation.
# They are separate axes on purpose — a rubric-graded proxy and a
# rubric-graded ground truth are different evaluators.
MY_PRODUCER_REGISTRY = (
    ProducerRegistration(
        property="refund_correctness",
        target="output",
        technique="llm_judge",
        evaluation_basis=PaaEvaluationBasis(kind="rubric", ref="refund_correctness_rubric"),
        epistemic_status="proxy",
        version="1.0.0",
        authority="advisory",
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

### Conformance

The conformance suite runs against the published contract artifacts rather
than fixtures of its own, so that "passes the published conformance suite" is
a claim about the contract and not about this repo's idea of it.

Those artifacts come from `paa-contracts` — the four normative schemas, the
positive fixture corpus, and the invalid-case tables, released from the paa.dev
repo. **It is not on PyPI yet, so today it takes two commands, not one:**

```bash
uv sync --extra conformance                                             # jsonschema
uv run --with ../paadotdev/packages/paa-contracts pytest conformance    # the artifacts
```

The `conformance` path is required, not decoration. `testpaths` is `tests`, so
a bare `uv run pytest` runs the unit suite and nothing else — which is what
lets the unit suite stay green for someone who cloned only this repo. The
conformance suite is opt-in by *invocation* rather than by skip marker: when it
is asked to run and the artifacts are absent, `paa_contracts` raises at import
and the run fails loudly, because a conformance suite reporting green over an
empty corpus is the one failure mode it must not have.

What it asserts, per fixture class:

- **Vocabulary parity** — every closed set the runtime hardcodes (positions,
  event types, deployments, placement modes, window kinds, execution modes,
  the `event_schema` stamp) equals the published schema's own.
- **Task declarations** — all four published declarations load, their fields
  survive the load, and each of the eleven `semantic` invalid cases is
  rejected. Each negative case is run against a registry derived from the
  *mutated* document, so registry resolution cannot reject a case before the
  rule under test does.
- **Event sequences** — every published motion history round-trips through the
  store field-identical, and is *producible*: driving `propose` then `approve`
  or `reject` regenerates it, re-deriving the same evidence content address
  from the bytes alone.
- **Evidence** — the runtime computes the content address each published
  artifact is filed under, and fails closed on the deliberately tampered one.

Three stages are deliberately not checked here, and the suite asserts their
case counts so the boundary moves only on purpose. `structural` cases are
Ajv's vocabulary and belong to the site's validator. `pinned` cases assert
facts about particular fixtures, which is corpus-pinning data no
implementation carries. The nineteen runtime-artifact `*_semantic` cases
describe validating a *foreign* document against a task index, and this
package has no import path to point them at — it governs motions it writes
itself, enforcing those rules at write time rather than by inspecting a
finished document.

One honest non-match: the published demotion history binds to a
`paa-decision-artifact`, while `demote` generates and content-addresses its own
evidence so an emergency demotion never blocks on an operator producing an
artifact first. Its event stream reproduces the published one in every field
except the two naming that evidence, and the suite asserts exactly that rather
than skipping the fixture.

The extra deliberately does not name `paa-contracts`. An unresolvable
requirement cannot be locked, and pinning it to a local path breaks plain
`uv sync` for anyone without a site checkout next door — uv resolves metadata
for every locked entry regardless of which extras are requested. The `--with`
form also builds and installs a real wheel, so it exercises the packaged data
path a release uses rather than a source-tree shortcut.

On publication `paa-contracts` joins the extra and the second command goes
away.

Both are **test-only**. Nothing in `paa_runtime` loads a JSON schema at
runtime, and a production install pulls neither.

## Status

`0.3.0`, tracking the `paa-task/0.2.1-draft` and `paa-autonomy-event/0.1.0-draft` schema families. Package and spec versions drift independently — the schema families a release targets are stated here and asserted by the conformance suite, not inferred from the package version.

`0.3.0` is a breaking change to the declaration access layer, and it is the change that made the sentence above true. `0.2.0` claimed the `paa-task/0.2.1-draft` family while implementing an older evaluator identity — a single `oracle` field where the contract has `evaluation_basis` and `epistemic_status` — and a `position_policy` requiring all four positions at fixed modes, where the contract admits any non-empty subset with per-evaluator placement overrides. It could not load a single published declaration. Building the conformance suite is what surfaced that; `PaaEvaluator`, `ProducerRegistration`, and `PaaPositionPolicy` changed shape to fix it. Nothing was published at `0.2.0`, so no consumer is stranded.
