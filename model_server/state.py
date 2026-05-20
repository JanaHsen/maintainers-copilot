"""Process-global holder for artifacts that survived the boot check.

Slice (b) only populates this from boot verification; the actual torch
model is loaded into it in slice (c) when /classify gains real inference.
"""

from __future__ import annotations

from model_server.boot_check import VerifiedArtifacts

_artifacts: VerifiedArtifacts | None = None


def set_artifacts(artifacts: VerifiedArtifacts) -> None:
    global _artifacts
    _artifacts = artifacts


def get_artifacts() -> VerifiedArtifacts:
    if _artifacts is None:
        raise RuntimeError(
            "model artifacts requested before boot verification completed"
        )
    return _artifacts


def clear_artifacts() -> None:
    """Drop the in-memory artifacts (used by shutdown and by tests)."""
    global _artifacts
    _artifacts = None
