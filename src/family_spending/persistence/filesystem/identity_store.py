from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass

from family_spending.domain.errors import DomainInvariantError
from family_spending.domain.transaction import SourceLink, validate_source_link_structure
from family_spending.persistence.filesystem.layout import StorageLayout


class IdentityStoreError(RuntimeError):
    """Raised when durable SourceLink identity history is malformed or cannot be persisted."""


@dataclass(frozen=True)
class FilesystemIdentityStore:
    """Persist durable SourceLinks as canonical filesystem identity state."""

    layout: StorageLayout

    @property
    def path(self):
        return self.layout.source_links

    def load(self) -> tuple[SourceLink, ...]:
        if not self.path.exists():
            return ()
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise IdentityStoreError(f"Unable to read identity state {self.path}: {exc}") from exc

        links: list[SourceLink] = []
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise IdentityStoreError(
                    f"Unable to parse identity state {self.path} at line {line_number}: {exc.msg}"
                ) from exc
            if not isinstance(raw, dict) or set(raw) != {
                "transaction_id",
                "source_record_id",
                "role",
            }:
                raise IdentityStoreError(
                    f"Invalid SourceLink in {self.path} at line {line_number}: {raw!r}"
                )
            try:
                links.append(
                    SourceLink(
                        transaction_id=raw["transaction_id"],
                        source_record_id=raw["source_record_id"],
                        role=raw["role"],
                    )
                )
            except (DomainInvariantError, TypeError, AttributeError) as exc:
                raise IdentityStoreError(
                    f"Invalid SourceLink in {self.path} at line {line_number}: {exc}"
                ) from exc

        result = tuple(links)
        try:
            validate_source_link_structure(result)
        except DomainInvariantError as exc:
            raise IdentityStoreError(f"Invalid identity state {self.path}: {exc}") from exc
        return result

    def replace(self, links: tuple[SourceLink, ...]) -> None:
        try:
            validate_source_link_structure(links)
        except DomainInvariantError as exc:
            raise IdentityStoreError(f"Invalid identity state: {exc}") from exc

        if not links:
            self.path.unlink(missing_ok=True)
            return

        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = "".join(
            json.dumps(
                {
                    "transaction_id": link.transaction_id,
                    "source_record_id": link.source_record_id,
                    "role": link.role,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
            for link in links
        )
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
                temp_path = handle.name
            os.replace(temp_path, self.path)
            temp_path = None
        except OSError as exc:
            raise IdentityStoreError(
                f"Unable to persist identity state {self.path}: {exc}"
            ) from exc
        finally:
            if temp_path is not None:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
