"""The Python contract list must agree with the site's JavaScript one.

``scripts/lib/contract-registry.mjs`` calls itself "the one ordered registry
of normative PAA contracts" and exists so the site's generator and validator
can never cover different contract sets. Adding a Python consumer adds a
second list, which reopens exactly the gap that registry was written to close
— unless something checks that the two agree.

This is that check. It parses the JS registry as text rather than importing
it, because the alternative is running Node from a Python test suite to learn
four strings. Text parsing is brittle in general; here it is brittle in the
right direction, since a registry edit that this parser cannot read fails the
test rather than silently matching.

These tests only ever run from a repo checkout — the wheel ships no tests —
so an unfindable registry is a failure, never a skip.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

import paa_contracts as contracts

_REGISTRY = Path(__file__).resolve().parents[3] / "scripts" / "lib" / "contract-registry.mjs"

# Matches the `contract('paa-task', 'PAATaskDeclaration'),` entries inside the
# exported CONTRACTS array, in source order.
_ENTRY_RE = re.compile(r"contract\(\s*'([^']+)'\s*,\s*'([^']+)'\s*\)")


def _js_registry_ids() -> tuple[str, ...]:
    source = _REGISTRY.read_text(encoding="utf-8")
    start = source.index("export const CONTRACTS")
    end = source.index("]", start)
    return tuple(match.group(1) for match in _ENTRY_RE.finditer(source[start:end]))


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


class TestRegistryParity:
    def test_the_site_registry_is_where_this_test_expects(self) -> None:
        assert _REGISTRY.is_file(), f"site contract registry not found at {_REGISTRY}"

    def test_the_parser_actually_found_entries(self) -> None:
        # Guards the failure mode this whole file is about: a regex that
        # matches nothing would make the comparison below trivially true.
        assert _js_registry_ids()

    def test_python_and_javascript_registries_agree_in_order(self) -> None:
        assert _js_registry_ids() == contracts.SCHEMA_IDS

    @pytest.mark.parametrize("schema_id", contracts.SCHEMA_IDS)
    def test_every_registered_contract_has_a_schema_file(self, schema_id: str) -> None:
        assert contracts.schema_path(schema_id).is_file()


class TestBuildHookCoverage:
    """The build hook must ship every directory the accessors read from.

    A fixture directory added to the site but not to ``_ARTIFACTS`` produces
    a package that works perfectly in the source tree — where the worktree
    fallback finds everything — and is missing data once installed. That is
    the worst shape of bug this package can have, so it is checked at the
    only moment it is cheap: before the release.
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
