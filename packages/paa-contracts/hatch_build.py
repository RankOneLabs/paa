"""Pull the contract artifacts into the build from wherever they actually are.

The artifacts are not vendored into this directory — they live at the repo
root, where the reference implementation's conformance suite reads them and
the site publishes them at their URLs. That single-source arrangement is the
whole point of the package (see README), but it means the build has to locate
them, and *where* they are depends on what is being built:

- Building either target from a repo checkout: they are two directories up,
  at ``schemas/`` and ``examples/``.
- Building a wheel from an unpacked sdist — which is what ``uv build`` does by
  default, to prove the sdist is self-sufficient — there is no checkout above.
  The sdist already carries the artifacts at ``src/paa_contracts/_data/``, so
  the wheel's ordinary package inclusion picks them up and this hook must do
  nothing rather than force-include paths that resolve outside the extract.

A static ``force-include`` table cannot express that fork, which is why this
is a hook. The check is for the artifacts themselves, not for a marker file or
an environment variable: the question the build actually needs answered is
"are the contract artifacts above me?", so that is the question it asks — and
because it asks that rather than "am I in a particular repo?", moving the
contract from the site repo into this one required no change here at all.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

# Relative to the repo root: the directories that constitute the published
# contract, and where each lands inside the package.
_ARTIFACTS: tuple[tuple[str, str], ...] = (
    ("schemas", "schemas"),
    ("examples/paa-tasks", "examples/paa-tasks"),
    ("examples/runtime-conformance", "examples/runtime-conformance"),
)


class ContractArtifactsHook(BuildHookInterface):  # type: ignore[type-arg]
    PLUGIN_NAME = "contract-artifacts"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        repo_root = Path(self.root).resolve().parents[1]

        # Both, not either: a directory named `schemas` above an unpacked
        # sdist would otherwise be enough to convince the build it had found
        # a contract checkout and force-include a partial, wrong corpus.
        looks_like_repo_checkout = (repo_root / "schemas").is_dir() and (
            repo_root / "examples" / "runtime-conformance"
        ).is_dir()
        if not looks_like_repo_checkout:
            return

        # The sdist mirrors the source layout so that a wheel built from the
        # unpacked sdist finds the data under the package it already ships.
        prefix = "src/paa_contracts/_data" if self.target_name == "sdist" else "paa_contracts/_data"

        force_include = build_data.setdefault("force_include", {})
        for source, target in _ARTIFACTS:
            force_include[str(repo_root / source)] = f"{prefix}/{target}"
