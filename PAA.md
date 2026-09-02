# paa-runtime mapped to PAA

`paa-runtime` is a complete implementation of PAA's autonomy-transition
control plane. It is not an evaluator framework or an agent runner. This
document maps its public surfaces to the specification and names the places
where the implementation deliberately stops.

## Surface mapping

| Runtime surface | PAA primitive | What is implemented |
|---|---|---|
| `RuntimeConfig`, `load_paa_declarations` | Task declaration | Loads versioned task, evaluator, position-policy, promotion, demotion, deployment, and scope declarations. Consumer-supplied producer registration binds declared evaluator identities to real code. |
| `ProducerRegistration` | Evaluator identity and authority | Checks the complete evaluator identity: property, target, technique, evaluation basis, epistemic status, version, and authority. It records that a producer exists; it does not execute it. |
| `propose`, `approve`, `reject` | Promotion decision and authority change | Records a proposed transition, requires a separate approval or rejection, re-verifies evidence on approval, rejects stale proposals, and commits approval plus position change atomically. |
| `demote` | Emergency demotion | Performs the declared one-step demotion in one command and generates its evidence record so incident response is not blocked on preparing an artifact first. |
| `resolve_current_position`, `show` | Gate position | Folds the exact task/version/scope position from append-only history. Current position is never stored as mutable state. |
| `list_motions`, `Motion` | Decision artifact projection | Projects motion status and operator-visible decision data from events; no separate mutable motion table exists. |
| `AutonomyEvent`, `EventStore` | Autonomy event | Uses the contract's fourteen-field event representation and an implementation protocol with explicit ordering, transaction, uniqueness, and append-only semantics. |
| `SqliteEventStore` | Evidence/event log substrate | Supplies the default append-only SQLite store, including storage-level update/delete rejection. |
| `store_evidence`, `verify_evidence` | Evidence record binding | Content-addresses exact evidence bytes with SHA-256 and fails closed on missing or changed bytes. |
| `import_events` | Archive replay | Imports already validated contract-shaped events without regenerating identifiers or timestamps. The legacy conformance capture proves field and projection continuity across extraction. |
| `paa-contracts` conformance suite | Published contract | Checks schema vocabulary, declarations, event histories, evidence addressing, invalid semantic cases, and the pre-cutover capture against the same packaged corpus. |

## Lifecycle coverage

The runtime implements the authority-changing part of the lifecycle:

1. A consumer produces evaluator verdicts and assembles an evidence artifact.
2. `propose` binds that artifact to the declared transition.
3. An operator calls `approve` or `reject`.
4. Approval and `position_changed` commit together.
5. Governed code calls `resolve_current_position` before performing an effect.
6. `demote` provides the declared emergency path back toward human control.

The runtime does not automatically promote when a threshold is reached.
Promotion rules are evidence requirements for operator judgment, not executable
policy in this implementation.

## Evidence for the mapping

The conformance suite uses the published `paa-contracts` schemas and fixtures,
not a runtime-owned copy. It proves that published histories round-trip without
field loss, that the lifecycle can produce them, that content addresses are
re-derived from bytes, and that runtime-owned negative cases fail for the
published reason.

`examples/legacy-archive/pre-cutover-capture.json` adds the consumer-boundary
proof. It was generated with the source consumer's pre-cutover lifecycle implementation at
commit `721c37facac64f12a164e510c9a0aa647a960cba`, then imported into the
extracted runtime. The test reproduces its event rows, motion projection, and
resolved position exactly.

The source consumer's production `autonomy_events` table contained zero rows at cutover. The
capture is therefore evidence from the real pre-cutover implementation, not a
claim that a production autonomy transition occurred. Keeping that distinction
in the artifact is part of the citation bar.

## Honest non-matches

### Evaluator verdict production

The runtime validates evaluator identities and producer registration but does
not run graders, judges, invariants, or human-review systems. Those are consumer
domain code. Each consumer owns the producers named by its declarations.

### Promotion-rule evaluation

Promotion thresholds and windows are loaded and retained, but the runtime does
not calculate whether a window has passed. `approve` records an operator's
judgment that the submitted evidence satisfies the declaration.

### Structural schema validation

The runtime performs semantic validation needed by lifecycle operations. It
does not embed a JSON Schema engine or reproduce Ajv's error vocabulary.
Structural validation belongs to `paa-contracts` consumers and the paa.dev
contract pipeline. This keeps the runtime independent of one schema-validator
implementation.

### Worker identity

Events record an actor selected explicitly, from a configured environment
variable, or from the OS login. The current contract has no durable worker
identity or attestation field, so an evidence window cannot prove which worker
produced every verdict. That requires a future contract revision.

### Governed-effect atomicity with the default store

The default `SqliteEventStore` cannot atomically couple a position read to an
effect committed in a consumer's separate database. A demotion can land after
the read and before that effect. The event history remains valid, but the
in-flight effect used a stale permit.

`EventStore` exists for consumers that cannot accept that window. A consumer can
implement the protocol over its own database, so the position read authorizing
a publication and the publication claim share one `BEGIN IMMEDIATE` lock
domain. The general runtime exposes this escape hatch; it cannot manufacture
cross-database atomicity for every consumer.

### Foreign finished-document validation

The runtime enforces event and decision invariants while writing its own
history. It does not offer a general API for validating arbitrary finished
decision artifacts from another implementation. Contract-level structural and
cross-document corpus validation remains in the published conformance tooling.

## Scope of the claim

The accurate claim is:

> `paa-runtime` implements PAA's declared autonomy-transition lifecycle and
> passes the published conformance corpus, including replay of a history
> captured from the source consumer's pre-cutover implementation.

It is not a claim that the runtime implements evaluation, worker attestation,
or every consumer's governed effect.
