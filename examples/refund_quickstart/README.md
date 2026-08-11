# Refund approval quickstart

This synthetic example walks one declared task through promotion and emergency
demotion. It uses only `RuntimeConfig`, `SqliteEventStore`, producer
registration, and the lifecycle API.

From a checkout of this repository:

```bash
uv sync
uv run python examples/refund_quickstart/run.py
```

The script creates an isolated temporary database and evidence tree, proposes
`hitl -> hotl`, approves it, then demotes `hotl -> hitl`. It does not modify the
repository. `refund_approval.v1.yaml` remains in the shared contract corpus so
the quickstart, conformance suite, and paa.dev schema reference use one fixture.

This is adoption-oriented synthetic pedagogy. Scout's separately labeled
pre-cutover capture is the cross-implementation evidence artifact.
