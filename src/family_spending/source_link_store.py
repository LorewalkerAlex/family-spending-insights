from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from family_spending.transactions import TransactionSourceLink

TRANSACTION_SOURCE_LINKS_FILE = Path("data/transaction_source_links.jsonl")


class SourceLinkStoreError(RuntimeError):
    """Raised when persisted SourceRecord-to-Transaction relationships are malformed or inconsistent."""


def read_transaction_source_links(
    path: Path = TRANSACTION_SOURCE_LINKS_FILE,
) -> tuple[TransactionSourceLink, ...]:
    """Use no prior links on first migration run so the existing CMB pipeline can bootstrap identities normally."""
    if not path.exists():
        return ()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SourceLinkStoreError(f"Unable to read source links {path}: {exc}") from exc

    links: list[TransactionSourceLink] = []
    seen_source_ids: set[str] = set()
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SourceLinkStoreError(
                f"Unable to parse source links {path} at line {line_number}: {exc.msg}"
            ) from exc
        if not isinstance(raw, dict) or set(raw) != {"transaction_id", "source_record_id", "role"}:
            raise SourceLinkStoreError(
                f"Invalid source link in {path} at line {line_number}: {raw!r}"
            )
        transaction_id = raw["transaction_id"]
        source_record_id = raw["source_record_id"]
        role = raw["role"]
        if not isinstance(transaction_id, str) or not transaction_id.strip():
            raise SourceLinkStoreError(f"Invalid transaction_id in {path} at line {line_number}")
        if not isinstance(source_record_id, str) or not source_record_id.strip():
            raise SourceLinkStoreError(f"Invalid source_record_id in {path} at line {line_number}")
        if role not in ("authoritative", "supporting"):
            raise SourceLinkStoreError(f"Invalid role in {path} at line {line_number}: {role!r}")
        if source_record_id in seen_source_ids:
            raise SourceLinkStoreError(
                f"Source record {source_record_id!r} is linked more than once in {path}"
            )
        seen_source_ids.add(source_record_id)
        links.append(TransactionSourceLink(transaction_id, source_record_id, role))
    return tuple(links)


def write_transaction_source_links(
    links: tuple[TransactionSourceLink, ...],
    path: Path = TRANSACTION_SOURCE_LINKS_FILE,
) -> None:
    """Persist relation history atomically because it preserves Transaction identity across later source arrivals."""
    seen_source_ids: set[str] = set()
    authoritative_by_transaction: set[str] = set()
    lines: list[str] = []
    for link in links:
        if link.source_record_id in seen_source_ids:
            raise SourceLinkStoreError(
                f"Source record {link.source_record_id!r} is linked more than once"
            )
        seen_source_ids.add(link.source_record_id)
        if link.role == "authoritative":
            if link.transaction_id in authoritative_by_transaction:
                raise SourceLinkStoreError(
                    f"Transaction {link.transaction_id!r} has multiple authoritative links"
                )
            authoritative_by_transaction.add(link.transaction_id)
        lines.append(
            json.dumps(
                {
                    "transaction_id": link.transaction_id,
                    "source_record_id": link.source_record_id,
                    "role": link.role,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(f"{line}\n" for line in lines)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
