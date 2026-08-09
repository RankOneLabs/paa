"""Tests for paa_runtime.evidence's content-addressed evidence storage."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from paa_runtime.evidence import (
    EvidenceError,
    compute_sha256,
    evidence_ref_for,
    read_evidence_bytes,
    store_evidence,
    verify_evidence,
)


class TestComputeSha256:
    def test_matches_hashlib(self) -> None:
        data = b"hello world"
        assert compute_sha256(data) == hashlib.sha256(data).hexdigest()

    def test_is_lowercase_hex(self) -> None:
        digest = compute_sha256(b"anything")
        assert digest == digest.lower()
        assert len(digest) == 64
        assert all(c in "0123456789abcdef" for c in digest)


class TestEvidenceRefFor:
    def test_shape(self) -> None:
        sha = "a" * 64
        assert evidence_ref_for(sha) == f"evidence/paa/{sha}/evidence.json"


class TestReadEvidenceBytes:
    def test_reads_regular_file(self, tmp_path: Path) -> None:
        source = tmp_path / "report.json"
        source.write_bytes(b'{"ok": true}')
        assert read_evidence_bytes(source) == b'{"ok": true}'

    def test_missing_file_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(EvidenceError, match="not a regular file"):
            read_evidence_bytes(tmp_path / "missing.json")

    def test_directory_rejected(self, tmp_path: Path) -> None:
        directory = tmp_path / "adir"
        directory.mkdir()
        with pytest.raises(EvidenceError, match="not a regular file"):
            read_evidence_bytes(directory)

    def test_symlink_rejected(self, tmp_path: Path) -> None:
        target = tmp_path / "real.json"
        target.write_bytes(b"data")
        link = tmp_path / "link.json"
        link.symlink_to(target)
        with pytest.raises(EvidenceError, match="not a regular file"):
            read_evidence_bytes(link)


class TestStoreEvidence:
    def test_stores_at_content_address(self, tmp_path: Path) -> None:
        data = b'{"report": "promotion evidence"}'
        ref, sha256 = store_evidence(data, root=tmp_path)

        assert sha256 == compute_sha256(data)
        assert ref == f"evidence/paa/{sha256}/evidence.json"
        stored_path = tmp_path / "evidence" / "paa" / sha256 / "evidence.json"
        assert stored_path.is_file()
        assert stored_path.read_bytes() == data

    def test_no_leftover_temp_files(self, tmp_path: Path) -> None:
        _, sha256 = store_evidence(b"payload", root=tmp_path)
        entries = list((tmp_path / "evidence" / "paa" / sha256).iterdir())
        assert entries == [tmp_path / "evidence" / "paa" / sha256 / "evidence.json"]

    def test_reproposing_identical_bytes_is_a_no_op(self, tmp_path: Path) -> None:
        data = b"same bytes"
        ref1, sha1 = store_evidence(data, root=tmp_path)
        ref2, sha2 = store_evidence(data, root=tmp_path)
        assert (ref1, sha1) == (ref2, sha2)

    def test_content_address_collision_with_different_bytes_rejected(
        self, tmp_path: Path
    ) -> None:
        data = b"original bytes"
        _, sha256 = store_evidence(data, root=tmp_path)
        stored_path = tmp_path / "evidence" / "paa" / sha256 / "evidence.json"
        # Simulate tamper: overwrite the stored bytes without going through
        # store_evidence, then try to "re-store" the original data again —
        # the mismatch must be caught, not silently overwritten.
        stored_path.write_bytes(b"tampered bytes")
        with pytest.raises(EvidenceError, match="collision"):
            store_evidence(data, root=tmp_path)

    def test_different_bytes_get_different_addresses(self, tmp_path: Path) -> None:
        ref1, sha1 = store_evidence(b"one", root=tmp_path)
        ref2, sha2 = store_evidence(b"two", root=tmp_path)
        assert ref1 != ref2
        assert sha1 != sha2


class TestVerifyEvidence:
    def test_round_trips_stored_evidence(self, tmp_path: Path) -> None:
        data = b'{"report": "demotion evidence"}'
        ref, sha256 = store_evidence(data, root=tmp_path)
        assert verify_evidence(ref, sha256, root=tmp_path) == data

    def test_missing_file_rejected(self, tmp_path: Path) -> None:
        sha256 = "b" * 64
        with pytest.raises(EvidenceError, match="missing"):
            verify_evidence(evidence_ref_for(sha256), sha256, root=tmp_path)

    def test_hash_mismatch_between_ref_and_recorded_hash_rejected(
        self, tmp_path: Path
    ) -> None:
        ref, _sha256 = store_evidence(b"data", root=tmp_path)
        other_sha = "c" * 64
        with pytest.raises(EvidenceError, match="does not match"):
            verify_evidence(ref, other_sha, root=tmp_path)

    def test_tampered_bytes_on_disk_rejected(self, tmp_path: Path) -> None:
        ref, sha256 = store_evidence(b"original", root=tmp_path)
        stored_path = tmp_path / "evidence" / "paa" / sha256 / "evidence.json"
        stored_path.write_bytes(b"tampered")
        with pytest.raises(EvidenceError, match="tampered"):
            verify_evidence(ref, sha256, root=tmp_path)

    @pytest.mark.parametrize(
        "bad_ref",
        [
            "evidence/paa/../../../etc/passwd",
            "evidence/paa/not-a-sha/evidence.json",
            "evidence/other/" + "a" * 64 + "/evidence.json",
            "/etc/passwd",
            "evidence/paa/" + "a" * 63 + "/evidence.json",
            "evidence/paa/" + "A" * 64 + "/evidence.json",
            "evidence/paa/" + "a" * 64 + "/../../../evidence.json",
        ],
    )
    def test_malformed_or_traversal_refs_rejected(self, tmp_path: Path, bad_ref: str) -> None:
        with pytest.raises(EvidenceError, match="not a well-formed"):
            verify_evidence(bad_ref, "a" * 64, root=tmp_path)

    def test_root_is_required_keyword_argument(self) -> None:
        # No silent default evidence root: omitting root must fail loudly at
        # the call boundary rather than writing into some fallback location.
        with pytest.raises(TypeError):
            store_evidence(b"x")  # type: ignore[call-arg]


class TestConcurrentPublish:
    """The collision check must survive losing the race to another writer.

    The fast-path ``dest.exists()`` read cannot be the guard: a writer can
    create the destination between that read and the publish. Publishing
    with ``os.replace`` would silently overwrite whatever arrived in the
    meantime — including the mismatched bytes this module promises to
    fail on. ``os.link`` fails instead, routing the race back through the
    same content check.
    """

    @staticmethod
    def _racing_link(written: bytes) -> object:
        """An os.link that loses the race: another writer creates the
        destination first, then this link fails as it would in reality."""

        def _link(src: str, dst: str) -> None:
            Path(dst).write_bytes(written)
            raise FileExistsError(17, "File exists", src, None, dst)

        return _link

    def test_losing_the_race_to_different_bytes_fails_closed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(os, "link", self._racing_link(b"tampered"))
        with pytest.raises(EvidenceError, match="content-address collision"):
            store_evidence(b"data", root=tmp_path)

    def test_losing_the_race_to_identical_bytes_succeeds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Two writers storing the same artifact is the common case and is
        not an error — content addressing makes the loser's work
        redundant, not wrong."""
        monkeypatch.setattr(os, "link", self._racing_link(b"data"))
        ref, sha256 = store_evidence(b"data", root=tmp_path)
        assert ref == evidence_ref_for(sha256)
        assert verify_evidence(ref, sha256, root=tmp_path) == b"data"

    def test_lost_race_leaves_no_temp_file_behind(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(os, "link", self._racing_link(b"data"))
        store_evidence(b"data", root=tmp_path)
        assert list(tmp_path.rglob(".evidence-*.tmp")) == []
