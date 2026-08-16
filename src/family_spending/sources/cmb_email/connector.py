from __future__ import annotations

from typing import Protocol


class CmbEmailConnector(Protocol):
    """Acquire raw mailbox messages; persistence and parsing remain separate responsibilities."""

    def fetch_raw_messages(self) -> tuple[bytes, ...]: ...
