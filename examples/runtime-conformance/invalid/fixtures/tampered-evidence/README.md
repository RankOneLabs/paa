# Deliberately tampered fixture

`evidence/paa/cd85082f0ef66db18183acd3e350668078d31cbb6ff68683a9efcb552157662c/evidence.json`
in this directory intentionally holds a *different* evidence record's bytes
(case-1002's LLM evidence) filed under case-1001's real content-address
directory name. It exists only so `scripts/validate-paa-contracts.mjs`'s
tamper-detection self-check has a genuine byte/hash mismatch to resolve
against — see the `canonicalByteVerify:tamperCheck` stage. It is kept
outside `examples/runtime-conformance/evidence-records/` so it is never picked up
by the positive-fixture discovery walk.
