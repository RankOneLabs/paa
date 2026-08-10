"""``SCHEMA_IDS`` must describe the contracts that actually exist.

This file used to assert parity between ``SCHEMA_IDS`` and the JS list in the
site's ``scripts/lib/contract-registry.mjs``, parsed as text. That check made
sense while both lists lived in one repo: two lists serving two languages, and
something had to stop them diverging.

They no longer live in one repo, and the fix was not to copy the JS file here
so the old comparison would still resolve. Two lists agreeing with each other
says nothing if both are wrong; two lists each agreeing with the *artifacts*
says everything, and needs no cross-repo coupling at all. So this side checks
``SCHEMA_IDS`` against ``schemas/``, the site checks its own list against the
schemas in the installed package, and neither has to know the other exists.

The stronger property is the one the old test could not have: a contract added
to ``schemas/`` and forgotten in ``SCHEMA_IDS`` now fails here. Under the old
comparison it passed, as long as both lists forgot it equally.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import paa_contracts as contracts


def _schema_ids_on_disk() -> set[str]:
    return {
        path.name.removesuffix(".schema.json")
        for path in contracts.SCHEMAS_ROOT.glob("*.schema.json")
    }


def _hook_artifacts() -> tuple[tuple[str, str], ...]:
    """``_ARTIFACTS`` from hatch_build.py, read without importing it.

    The module imports hatchling, which lives in the build environment and
    not the test one. Reading the literal with ``ast`` gets the constant
    without needing the build backend installed just to look at a tuple —
    and without executing anything.
    """
    hook = Path(__file__).resolve().parents[1] / "hatch_build.py"
    tree = ast.parse(hook.read_text(encoding="utf-8"))
    for node in tree.body:
        target = getattr(node, "target", None)
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(target, ast.Name)
            and target.id == "_ARTIFACTS"
        ):
            value: tuple[tuple[str, str], ...] = ast.literal_eval(node.value)
            return value
    raise AssertionError(f"no _ARTIFACTS assignment found in {hook}")


class TestRegistryCoverage:
    def test_the_schemas_directory_is_not_empty(self) -> None:
        # Guards the failure mode this whole file is about: an empty glob
        # would make the comparison below trivially true against an empty
        # SCHEMA_IDS, and non-trivially false against a real one — but only
        # the second is loud, so say it directly.
        assert _schema_ids_on_disk()

    def test_schema_ids_are_exactly_the_contracts_on_disk(self) -> None:
        assert _schema_ids_on_disk() == set(contracts.SCHEMA_IDS)

    def test_schema_ids_has_no_duplicates(self) -> None:
        # The comparison above is against a set, so a repeated entry would
        # survive it.
        assert len(contracts.SCHEMA_IDS) == len(set(contracts.SCHEMA_IDS))

    @pytest.mark.parametrize("schema_id", contracts.SCHEMA_IDS)
    def test_every_registered_contract_has_a_schema_file(self, schema_id: str) -> None:
        assert contracts.schema_path(schema_id).is_file()


class TestBuildHookCoverage:
    """The build hook must ship every directory the accessors read from.

    A fixture directory added to the corpus but not to ``_ARTIFACTS``
    produces a package that works perfectly in the source tree — where the
    worktree fallback finds everything — and is missing data once installed.
    That is the worst shape of bug this package can have, so it is checked at
    the only moment it is cheap: before the release.
    """

    def test_hook_ships_every_root_the_accessors_read(self) -> None:
        artifacts = _hook_artifacts()
        shipped = {target for _, target in artifacts}
        repo_root = Path(__file__).resolve().parents[3]
        needed = {
            str(root.relative_to(contracts.CONTRACTS_ROOT))
            for root in (contracts.SCHEMAS_ROOT, contracts.TASK_FIXTURES_ROOT,
                         contracts.RUNTIME_FIXTURES_ROOT)
        }
        assert needed <= shipped
        # And every shipped path is real, so the hook cannot name a directory
        # that quietly contributes nothing.
        assert all((repo_root / source).is_dir() for source, _ in artifacts)

    def test_the_artifact_map_was_actually_read(self) -> None:
        assert _hook_artifacts()
