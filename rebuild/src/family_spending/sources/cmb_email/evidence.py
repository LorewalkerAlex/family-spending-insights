from __future__ import annotations

import hashlib
from dataclasses import dataclass


class CmbEmailEvidenceError(ValueError):
    """Raised when raw CMB evidence cannot satisfy its immutable bytes contract."""


@dataclass(frozen=True)
class CmbEmailEvidence:
    """Immutable raw EML bytes whose content hash is the canonical evidence identity."""

    raw_bytes: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.raw_bytes, bytes) or not self.raw_bytes:
            raise CmbEmailEvidenceError("CMB email evidence must contain non-empty raw bytes")

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.raw_bytes).hexdigest()

    @property
    def identity(self) -> str:
        return f"sha256:{self.digest}"

    @property
    def filename(self) -> str:
        """Use content addressing so filenames never become business identity inputs."""
        return f"{self.digest}.eml"
