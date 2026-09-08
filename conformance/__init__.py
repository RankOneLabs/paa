"""Conformance of this implementation against the published PAA contract.

Every fixture, schema, and negative case here comes from ``paa-contracts``
— the artifacts paa.dev publishes — rather than from fixtures of this
repo's own, so that "passes the published conformance suite" is a claim
about the contract, not about this repo's idea of it.

This package is deliberately not collected by ``uv run pytest``. The unit
suite under ``tests/`` must pass for someone who cloned only this repo,
and these modules import ``paa_contracts``, which is a separate install.
Run them explicitly:

    uv sync --extra conformance
    uv run --with ../paadotdev/packages/paa-contracts pytest conformance

Opt-in by *invocation*, never by skip marker. A suite that skips when its
fixtures are missing reports green over an empty corpus, so when these
modules are asked to run and the artifacts are absent, ``paa_contracts``
raises at import and the run fails loudly.
"""
