from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from family_spending.persistence.filesystem.layout import StorageLayout
from family_spending.sources.cmb_email.evidence import CmbEmailEvidence


class CmbEmailEvidenceStoreError(RuntimeError):
    """Raised when immutable raw EML persistence violates content-addressed storage."""


@dataclass(frozen=True)
class CmbEmailEvidenceStore:
    """Persist raw CMB EML bytes immutably under their SHA-256 content address."""

    layout: StorageLayout

    def add(self, evidence: CmbEmailEvidence) -> bool:
        """Persist evidence once; identical retries are idempotent and never overwrite bytes."""
        directory = self.layout.cmb_email_evidence
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / evidence.filename
        if target.exists():
            try:
                existing = target.read_bytes()
            except OSError as exc:
                raise CmbEmailEvidenceStoreError(
                    f"Unable to read existing CMB evidence {target}: {exc}"
                ) from exc
            if existing != evidence.raw_bytes:
                raise CmbEmailEvidenceStoreError(
                    f"CMB evidence content-address collision or corruption at {target}"
                )
            return False

        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=directory,
                prefix=f".{evidence.digest}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                handle.write(evidence.raw_bytes)
                handle.flush()
                os.fsync(handle.fileno())
                temp_path = Path(handle.name)

            # Canonical runtime is single-writer, but re-checking avoids replacing
            # evidence if a retry managed to publish the same content first.
            if target.exists():
                existing = target.read_bytes()
                if existing != evidence.raw_bytes:
                    raise CmbEmailEvidenceStoreError(
                        f"CMB evidence content-address collision or corruption at {target}"
                    )
                return False

            os.replace(temp_path, target)
            temp_path = None
            return True
        except OSError as exc:
            raise CmbEmailEvidenceStoreError(
                f"Unable to persist CMB evidence {target}: {exc}"
            ) from exc
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    def load_all(self) -> tuple[CmbEmailEvidence, ...]:
        """Load exact bytes and reject any .eml whose filename disagrees with its content hash."""
        directory = self.layout.cmb_email_evidence
        if not directory.exists():
            return ()

        evidence_items: list[CmbEmailEvidence] = []
        for path in sorted(directory.glob("*.eml"), key=lambda item: item.name):
            try:
                evidence = CmbEmailEvidence(path.read_bytes())
            except (OSError, ValueError) as exc:
                raise CmbEmailEvidenceStoreError(
                    f"Unable to load CMB evidence {path}: {exc}"
                ) from exc
            if path.name != evidence.filename:
                raise CmbEmailEvidenceStoreError(
                    f"CMB evidence filename does not match content hash: {path}"
                )
            evidence_items.append(evidence)
        return tuple(evidence_items)
