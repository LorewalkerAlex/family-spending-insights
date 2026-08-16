from __future__ import annotations

import unittest

from family_spending.sources.cmb_email.acquisition import CmbEmailAcquirer
from family_spending.sources.cmb_email.evidence import CmbEmailEvidence


class _Connector:
    def __init__(self, messages: tuple[bytes, ...]) -> None:
        self.messages = messages

    def fetch_raw_messages(self) -> tuple[bytes, ...]:
        return self.messages


class _Writer:
    def __init__(self) -> None:
        self.identities: set[str] = set()

    def add(self, evidence: CmbEmailEvidence) -> bool:
        if evidence.identity in self.identities:
            return False
        self.identities.add(evidence.identity)
        return True


class CmbEmailAcquisitionTests(unittest.TestCase):
    def test_acquirer_persists_raw_evidence_idempotently_without_reconciliation(self) -> None:
        writer = _Writer()
        acquirer = CmbEmailAcquirer(_Connector((b"first", b"first", b"second")), writer)
        first = acquirer.acquire()
        second = acquirer.acquire()

        self.assertEqual(first.source_type, "cmb_email")
        self.assertEqual(first.fetched_count, 3)
        self.assertEqual(first.added_count, 2)
        self.assertEqual(second.fetched_count, 3)
        self.assertEqual(second.added_count, 0)
        self.assertEqual(len(writer.identities), 2)


if __name__ == "__main__":
    unittest.main()
