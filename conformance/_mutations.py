"""Applying the published invalid-case mutations to a positive fixture.

Each invalid case is a positive fixture plus a short list of edits, so a
negative corpus stays readable as a diff from something valid rather than
as ninety-five hand-written documents that drift apart. Applying those
edits is the consumer's job, and this module is this implementation's
copy of it — the same three verbs the site's json-pointer.mjs applies.
"""

from __future__ import annotations

import copy
from collections.abc import Sequence
from typing import Any

__all__ = ["MutationError", "apply_mutations"]


class MutationError(ValueError):
    """A case's mutation could not be applied to its base fixture.

    Raised rather than skipped. A mutation that does not apply means the
    case and the fixture it names have drifted apart, and a suite that
    quietly dropped such a case would report green while testing less
    than it claims.
    """


def _unescape(token: str) -> str:
    # RFC 6901: ~1 is "/" and ~0 is "~", and the order matters — unescaping
    # ~0 first would turn "~01" into "~1" and then into "/".
    return token.replace("~1", "/").replace("~0", "~")


def _descend(document: Any, pointer: str) -> tuple[Any, str]:
    """Resolve *pointer* to its container and the final token."""
    tokens = [_unescape(token) for token in pointer.split("/")[1:]]
    if not tokens:
        raise MutationError(f"pointer {pointer!r} does not name a member")

    node = document
    for token in tokens[:-1]:
        try:
            node = node[int(token)] if isinstance(node, list) else node[token]
        except (KeyError, IndexError, ValueError) as exc:
            raise MutationError(f"pointer {pointer!r} does not resolve: {exc}") from exc
    return node, tokens[-1]


def _read(document: Any, pointer: str) -> Any:
    node, last = _descend(document, pointer)
    try:
        return node[int(last)] if isinstance(node, list) else node[last]
    except (KeyError, IndexError, ValueError) as exc:
        raise MutationError(f"pointer {pointer!r} does not resolve: {exc}") from exc


def _assign(node: Any, token: str, value: Any) -> None:
    if not isinstance(node, list):
        node[token] = value
        return
    # "-" is RFC 6901's one-past-the-end position: append.
    if token == "-":
        node.append(value)
    else:
        node.insert(int(token), value)


def apply_mutations(base: Any, mutations: Sequence[Any]) -> Any:
    """*base* with every mutation applied, as a new document.

    The input is deep-copied, so one case's edits can never leak into the
    next case that names the same base fixture.
    """
    document = copy.deepcopy(base)

    for mutation in mutations:
        kind = mutation["kind"]
        node, last = _descend(document, mutation["path"])

        if kind == "set":
            _assign(node, last, copy.deepcopy(mutation["value"]))
        elif kind == "remove":
            try:
                if isinstance(node, list):
                    del node[int(last)]
                else:
                    del node[last]
            except (KeyError, IndexError, ValueError) as exc:
                raise MutationError(
                    f"remove at {mutation['path']!r} does not resolve: {exc}"
                ) from exc
        elif kind == "copy":
            _assign(node, last, copy.deepcopy(_read(document, mutation["from"])))
        else:
            raise MutationError(f"unknown mutation kind {kind!r}")

    return document
