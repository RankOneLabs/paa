"""Tests for the invalid-case mutation applier.

``conformance/_mutations.py`` is conformance-suite machinery, but it imports
nothing beyond the standard library, so its tests belong in the default suite
rather than behind the contract artifacts. That matters here: the error paths
below are not reachable from the published case tables — every one of the 95
cases carries a well-formed pointer — so nothing else would exercise them, and
a later refactor could drop the guards without a single test noticing.

The module's one promise is that a mutation which does not apply raises
MutationError. These are the ways that promise can be broken quietly.
"""

from __future__ import annotations

import pytest

from conformance._mutations import MutationError, apply_mutations


class TestPointerGuards:
    def test_pointer_without_a_leading_slash_is_rejected(self) -> None:
        # "a/b" splits to ["b"], which resolves against the document root:
        # without the guard this succeeds at editing a member the case never
        # named, which is worse than failing.
        with pytest.raises(MutationError, match="does not start with"):
            apply_mutations(
                {"a": {"b": 1}, "b": "untouched"},
                [{"kind": "set", "path": "a/b", "value": 2}],
            )

    def test_empty_pointer_names_no_member(self) -> None:
        with pytest.raises(MutationError, match="does not name a member"):
            apply_mutations({"a": 1}, [{"kind": "set", "path": "", "value": 2}])

    def test_descending_through_a_scalar_raises_mutation_error(self) -> None:
        # Indexing a string raises TypeError, which would otherwise escape
        # uncaught and break the module's contract.
        with pytest.raises(MutationError, match="does not resolve"):
            apply_mutations(
                {"s": "text"},
                [{"kind": "set", "path": "/s/x/y", "value": 1}],
            )

    def test_removing_an_absent_member_raises_mutation_error(self) -> None:
        with pytest.raises(MutationError, match="does not resolve"):
            apply_mutations({"a": 1}, [{"kind": "remove", "path": "/nope"}])

    def test_unknown_mutation_kind_is_rejected(self) -> None:
        with pytest.raises(MutationError, match="unknown mutation kind"):
            apply_mutations({"a": 1}, [{"kind": "rename", "path": "/a"}])


class TestApplication:
    def test_the_base_document_is_not_mutated(self) -> None:
        # One case's edits must never leak into the next case naming the
        # same base fixture.
        base = {"a": {"b": 1}}
        apply_mutations(base, [{"kind": "set", "path": "/a/b", "value": 2}])
        assert base == {"a": {"b": 1}}

    def test_set_replaces_a_member(self) -> None:
        result = apply_mutations({"a": 1}, [{"kind": "set", "path": "/a", "value": 2}])
        assert result == {"a": 2}

    def test_remove_deletes_a_list_element_by_index(self) -> None:
        result = apply_mutations(
            {"xs": [1, 2, 3]}, [{"kind": "remove", "path": "/xs/1"}],
        )
        assert result == {"xs": [1, 3]}

    def test_copy_appends_at_the_end_marker(self) -> None:
        # "-" is RFC 6901's one-past-the-end position.
        result = apply_mutations(
            {"xs": [{"v": 1}]},
            [{"kind": "copy", "path": "/xs/-", "from": "/xs/0"}],
        )
        assert result == {"xs": [{"v": 1}, {"v": 1}]}

    def test_copied_values_are_independent_of_their_source(self) -> None:
        result = apply_mutations(
            {"xs": [{"v": 1}]},
            [
                {"kind": "copy", "path": "/xs/-", "from": "/xs/0"},
                {"kind": "set", "path": "/xs/1/v", "value": 2},
            ],
        )
        assert result == {"xs": [{"v": 1}, {"v": 2}]}

    def test_escaped_tokens_resolve(self) -> None:
        # RFC 6901: ~1 is "/" and ~0 is "~".
        result = apply_mutations(
            {"a/b": 1, "c~d": 2},
            [
                {"kind": "set", "path": "/a~1b", "value": 10},
                {"kind": "set", "path": "/c~0d", "value": 20},
            ],
        )
        assert result == {"a/b": 10, "c~d": 20}
