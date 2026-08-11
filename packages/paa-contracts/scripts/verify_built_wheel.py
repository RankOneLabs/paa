#!/usr/bin/env python3
"""Prove a built wheel carries the working tree's contract artifacts verbatim.

The package's central claim is that it vendors nothing: the bytes the site
serves and the bytes an implementation loads are the same bytes from the same
commit. Everything else in the repo — the build hook, the force-include
arrangement, the no-copies rule — exists to make that true, and none of it is
checked by the unit tests, which run against the working tree and would pass
just as happily if the build shipped an empty package.

So this compares the built wheel against the tree in both directions. Missing
files catch a fixture directory added to the corpus but not to the hook's
_ARTIFACTS. Extra files catch a stale copy surviving in the build. Differing
bytes catch any transformation applied on the way in, which for
content-addressed artifacts is indistinguishable from corruption.

Usage: python scripts/verify_built_wheel.py dist/paa_contracts-<version>-*.whl
"""

from __future__ import annotations

import hashlib
import sys
import zipfile
from pathlib import Path

# Relative to the repo root; must stay in step with hatch_build.py's _ARTIFACTS.
ARTIFACT_DIRS = (
    "schemas",
    "examples/paa-tasks",
    "examples/runtime-conformance",
    "examples/scout-archive",
)

_DATA_MARKER = "/_data/"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2

    wheel_path = Path(argv[1])
    if not wheel_path.is_file():
        print(f"no such wheel: {wheel_path}", file=sys.stderr)
        return 2

    repo_root = Path(__file__).resolve().parents[3]
    wheel = zipfile.ZipFile(wheel_path)

    in_wheel = {
        name.split(_DATA_MARKER, 1)[1]: name
        for name in wheel.namelist()
        if _DATA_MARKER in name
    }
    in_tree = {
        str(path.relative_to(repo_root))
        for directory in ARTIFACT_DIRS
        for path in (repo_root / directory).rglob("*")
        if path.is_file()
    }

    problems: list[str] = []

    if not in_wheel:
        problems.append(
            "the wheel carries no contract artifacts at all — the build hook "
            "did not recognise this checkout as a contract tree"
        )

    for missing in sorted(in_tree - set(in_wheel)):
        problems.append(f"in the tree but not the wheel: {missing}")
    for extra in sorted(set(in_wheel) - in_tree):
        problems.append(f"in the wheel but not the tree: {extra}")

    for relative in sorted(in_tree & set(in_wheel)):
        packed = _digest(wheel.read(in_wheel[relative]))
        source = _digest((repo_root / relative).read_bytes())
        if packed != source:
            problems.append(
                f"bytes differ: {relative} "
                f"({source[:12]} in tree, {packed[:12]} packed)"
            )

    if problems:
        print(f"{wheel_path.name} does not match the working tree:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1

    print(f"{wheel_path.name}: {len(in_wheel)} artifacts, byte-identical to the working tree")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
