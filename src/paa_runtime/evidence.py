"""Content-addressed evidence storage for the PAA autonomy control plane.

Every motion (propose, approve, reject, demote) binds itself to the exact
bytes of one evidence artifact rather than a mutable file path: the bytes
are hashed once, written to ``evidence/paa/<sha256>/evidence.json`` via an
atomic temporary-file-and-rename, and referenced from then on by that
stable, repository-relative path plus the recorded hash. Approval re-reads
and re-hashes the file to prove it still matches what was proposed —
tamper or loss becomes a fail-closed error instead of silent drift.

This module does not validate evidence *content* against any schema:
paa_runtime.service accepts whatever non-empty bytes the operator supplies
at propose time, and task-specific report producers own their own
artifact shape.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path

_EVIDENCE_REF_RE = re.compile(r"^evidence/paa/([0-9a-f]{64})/evidence\.json$")


class EvidenceError(ValueError):
    """Evidence bytes are absent, malformed, mismatched, or tampered with.

    Raised for every failure mode this module guards against: a missing
    or non-regular source file, an evidence_ref outside evidence/paa or
    shaped as anything other than evidence/paa/<sha256>/evidence.json, a
    content-address collision (existing bytes differ from what the sha256
    names), a missing evidence file at approval/verification time, or a
    hash mismatch between the recorded digest and the bytes actually on
    disk. Callers must never catch this and substitute an invented or
    permissive default.
    """


def compute_sha256(data: bytes) -> str:
    """The lowercase hex SHA-256 digest of *data*."""
    return hashlib.sha256(data).hexdigest()


def read_evidence_bytes(path: Path) -> bytes:
    """Read the exact bytes of a regular evidence source file.

    Raises EvidenceError if *path* is missing, is a symlink, or is not a
    regular file — evidence must be exactly the bytes an operator points
    at, not whatever a symlink happens to resolve to at read time.
    """
    if path.is_symlink() or not path.is_file():
        raise EvidenceError(f"evidence source is not a regular file: {path}")
    return path.read_bytes()


def evidence_ref_for(sha256: str) -> str:
    """The stable repository-relative POSIX evidence_ref for *sha256*."""
    return f"evidence/paa/{sha256}/evidence.json"


def _resolve_evidence_path(evidence_ref: str, *, root: Path) -> tuple[Path, str]:
    """Validate *evidence_ref*'s shape and resolve it under *root*.

    Only the exact ``evidence/paa/<64-hex-lowercase>/evidence.json`` shape
    is accepted — this both rejects path traversal (no ``..``, no extra
    segments can survive the fullmatch) and confines every reference to
    the evidence/paa content-address root.
    """
    match = _EVIDENCE_REF_RE.fullmatch(evidence_ref)
    if match is None:
        raise EvidenceError(
            f"evidence_ref {evidence_ref!r} is not a well-formed "
            "evidence/paa/<sha256>/evidence.json reference"
        )
    sha256 = match.group(1)
    return root / "evidence" / "paa" / sha256 / "evidence.json", sha256


def store_evidence(data: bytes, *, root: Path) -> tuple[str, str]:
    """Content-address *data* under *root*/evidence/paa/<sha256>/evidence.json.

    Writes via a temporary file in the same directory followed by an
    atomic ``os.replace`` so a reader never observes a partial write. If
    the content address already exists, verifies the existing bytes equal
    *data* rather than overwriting — a collision on the hash with
    different bytes is a defect (SHA-256 collision or a caller passing
    mismatched data for a reused sha) and fails closed instead of
    silently accepting either version.

    Returns (evidence_ref, sha256) — evidence_ref is always the stable
    ``evidence/paa/<sha256>/evidence.json`` POSIX path regardless of
    *root* (which only controls where it physically lives, for test
    isolation).
    """
    sha256 = compute_sha256(data)
    dest, _ = _resolve_evidence_path(evidence_ref_for(sha256), root=root)
    dest_dir = dest.parent
    dest_dir.mkdir(parents=True, exist_ok=True)

    if dest.exists():
        existing = read_evidence_bytes(dest)
        if existing != data:
            raise EvidenceError(
                f"content-address collision at {dest}: existing bytes do not "
                f"match sha256 {sha256}"
            )
        return evidence_ref_for(sha256), sha256

    tmp_fd, tmp_name = None, None
    try:
        tmp_fd, tmp_name = tempfile.mkstemp(dir=dest_dir, prefix=".evidence-", suffix=".tmp")
        with os.fdopen(tmp_fd, "wb") as handle:
            tmp_fd = None
            handle.write(data)
        os.replace(tmp_name, dest)
        tmp_name = None
    finally:
        if tmp_fd is not None:
            os.close(tmp_fd)
        if tmp_name is not None and os.path.exists(tmp_name):
            os.unlink(tmp_name)

    return evidence_ref_for(sha256), sha256


def verify_evidence(
    evidence_ref: str, evidence_sha256: str, *, root: Path,
) -> bytes:
    """Read back and verify one stored evidence artifact.

    Confirms *evidence_ref* is a well-formed reference inside evidence/paa,
    that its embedded sha256 equals *evidence_sha256*, that the file exists,
    and that its actual bytes hash to that same digest. Returns the bytes
    on success; raises EvidenceError on any mismatch, absence, or tamper.
    """
    path, sha_from_ref = _resolve_evidence_path(evidence_ref, root=root)
    if sha_from_ref != evidence_sha256:
        raise EvidenceError(
            f"evidence_ref {evidence_ref!r} does not match recorded "
            f"evidence_sha256 {evidence_sha256!r}"
        )
    if not path.is_file():
        raise EvidenceError(f"evidence file is missing: {path}")
    data = read_evidence_bytes(path)
    actual = compute_sha256(data)
    if actual != evidence_sha256:
        raise EvidenceError(
            f"evidence at {path} has been tampered with: bytes hash to "
            f"{actual}, expected {evidence_sha256}"
        )
    return data


__all__ = [
    "EvidenceError",
    "compute_sha256",
    "evidence_ref_for",
    "read_evidence_bytes",
    "store_evidence",
    "verify_evidence",
]
