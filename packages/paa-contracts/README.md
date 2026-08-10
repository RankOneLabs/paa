# paa-contracts

The published contract artifacts of the [Progressive Autonomy Architecture](https://www.paa.dev): four normative JSON Schemas, the positive fixture corpus every implementation is checked against, and the table-driven invalid-case matrices.

No runtime logic, no dependencies. This package is data and honest paths to it.

## Why it exists

PAA is implementation-neutral by construction, which is only true if the contract is a thing implementations can *depend on* rather than each vendoring a copy of. So the dependency points one way and only one way:

```
paa-contracts  ←  paa-runtime          (reference implementation, conformance suite)
               ←  Scout                (production consumer, task-schema validation)
               ←  your implementation
```

The contract never depends on an implementation. A second implementation in any language gets its fixtures exactly the way the first one did.

## Install

Not on PyPI yet. Inside this repo it is a workspace member, so the reference
implementation's conformance suite gets it from an extra:

```bash
uv sync --extra conformance
```

From outside, install it from a checkout until it is published:

```bash
uv add --dev ./packages/paa-contracts         # path dependency
uv run --with ./packages/paa-contracts pytest   # or per-command
```

Both build and install a real wheel, so they exercise the packaged data path a release uses rather than a source-tree shortcut. On publication this becomes:

```bash
uv add --dev paa-contracts
```

Either way it is a **test dependency**. Nothing in a production PAA install needs to read a JSON schema at runtime, and a package that ships fixtures has no business in one.

## Use

```python
import paa_contracts as contracts

contracts.schema_version("paa-task")        # 'paa-task/0.2.1-draft'
schema = contracts.load_schema("paa-task")  # parsed, ready for jsonschema/Ajv/whatever

for path in contracts.task_declaration_paths():
    ...   # the four valid declaration fixtures

for path in contracts.autonomy_event_paths():
    ...   # five motion histories, one full event sequence per file

for case in contracts.invalid_cases("event", stage="event_semantic"):
    base = contracts.resolve_case_base("event", case)
    ...   # apply case['mutations'] to base, assert rejection matches case['expected']
```

`DATA_SOURCE` reports whether the artifacts came from installed package data (`"packaged"`) or a repo checkout (`"worktree"`). Both resolve to the same bytes; it is surfaced because it changes what a failure *means*, not what the answer is.

## The stage split

Every invalid case declares `expected.stage`, and that field is an ownership boundary rather than a label:

| Stage | Owner | Why |
|---|---|---|
| `structural` | the site's Ajv validator | Ajv's error keywords, JSON pointers, and `params` are Ajv's vocabulary. A Python suite asserting them would be testing a reimplementation of Ajv, not the contract. |
| `semantic`, `pinned` | task-declaration implementations | cross-field rules and pinned-fixture invariants, expressed in the contract's own terms |
| `evidence_semantic`, `decision_semantic`, `event_semantic` | runtime implementations | scope/version registration, identity drift, reference-hash agreement, motion ordering |

Filter with `invalid_cases(kind, stage=...)` and take only what you own. `case_stages(kind)` lists what a table actually contains, so a suite can fail loudly when a new stage appears rather than skipping it.

## Nothing here is a copy

The artifacts are not checked into this directory. `hatch_build.py` pulls them from the repo root at build time — that hook is where the inclusion logic lives, not `pyproject.toml`, which only registers it. So the files paa.dev serves at their published URLs, the files the validator reads, and the files a conformance suite loads are the same bytes from the same commit.

Vendoring copies here would put two sources of truth one careless commit apart and make every conformance claim a claim about the copy. Build-time inclusion makes "one source of truth" a property of the build instead of a rule someone has to remember.

The consequence: a wheel can only be built from a full checkout of this repo. That is correct — a release cut from anything less than the whole tree would be a partial contract.

## Contents

| Path | What |
|---|---|
| `schemas/` | `paa-task`, `paa-evidence-record`, `paa-decision-artifact`, `paa-autonomy-event` |
| `examples/paa-tasks/` | four valid declarations + 63 invalid cases |
| `examples/runtime-conformance/` | evidence records, decision artifacts, autonomy-event sequences, payload companion schemas, 32 invalid cases |
| `examples/runtime-conformance/invalid/fixtures/tampered-evidence/` | a deliberately byte-mismatched artifact, so tamper detection has something real to fail on |

## Versioning

The package version and the schema-family versions drift independently, on purpose — a packaging fix should not imply a contract revision. Read a family version from the schema that declares it:

```python
contracts.schema_version("paa-autonomy-event")   # 'paa-autonomy-event/0.1.0-draft'
```

## Development

Released from this repo, where the schemas and fixtures live alongside the reference implementation that is measured against them.

```bash
uv run pytest                      # includes registry parity against contract-registry.mjs
uv run ruff check src tests
uv run mypy src/paa_contracts
uv build                           # must run from a full repo checkout
```
