"""The entire construction surface for a paa_runtime consumer.

``RuntimeConfig`` is the one object a consumer builds to adopt the
package: every path, registry, and env-var name the runtime needs is an
explicit field here, supplied by the consumer at construction time.
There is no module-global state anywhere in paa_runtime and no
import-time path resolution — nothing defaults into a consumer's
repository, a package install directory, or a vendor's naming
convention. A new consumer installs the package, builds one
``RuntimeConfig``, and calls the lifecycle API; it never implements a
protocol and never hosts a table.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from paa_runtime.declarations import ProducerRegistration


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """Everything a consumer supplies to construct a working runtime."""

    # Directory of checked-in PAA task declaration YAML files, passed to
    # load_paa_declarations / get_paa_declaration.
    declarations_dir: Path

    # Root directory under which content-addressed evidence is read and
    # written, at evidence/paa/<sha256>/evidence.json beneath it.
    evidence_root: Path

    # The consumer's producer registry: which evaluator identities exist
    # and what produces their verdicts. Consumer domain data, not owned
    # by the runtime — see ProducerRegistration.
    registry: tuple[ProducerRegistration, ...]

    # Path to the SQLite database file backing the default EventStore.
    db_path: Path

    # Name of the environment variable the runtime reads to resolve the
    # acting operator's identity. Left to the consumer so the runtime
    # never hardcodes a vendor-specific variable name.
    actor_env_var: str = "PAA_ACTOR"


__all__ = ["RuntimeConfig"]
