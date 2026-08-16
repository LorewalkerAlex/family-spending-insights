from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from family_spending.persistence.filesystem.application_uow import FilesystemUnitOfWorkProvider
from family_spending.persistence.filesystem.layout import StorageLayout


class ApplicationUnitOfWorkProviderTests(unittest.TestCase):
    def test_each_application_scope_captures_only_its_durable_participants(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            layout = StorageLayout(Path(temp_dir).resolve())
            provider = FilesystemUnitOfWorkProvider(layout)
            expected = {
                "source_sync": (layout.source_links, layout.enrichment_decisions),
                "manual_input": (
                    layout.manual_evidence,
                    layout.source_links,
                    layout.enrichment_decisions,
                ),
                "enrichment": (layout.enrichment_decisions,),
                "mapping_review": (layout.merchant_mappings, layout.category_mappings),
                "schedule_rules": (layout.scheduled_rules, layout.schedule_execution),
                "scheduled_run": (
                    layout.scheduled_rules,
                    layout.schedule_execution,
                    layout.manual_evidence,
                    layout.source_links,
                    layout.enrichment_decisions,
                ),
                "feedback": (layout.feedback,),
            }
            for scope, paths in expected.items():
                with self.subTest(scope=scope):
                    unit = provider.open(scope, label=f"test {scope}")  # type: ignore[arg-type]
                    self.assertEqual(unit._paths, paths)  # exact filesystem adapter contract


if __name__ == "__main__":
    unittest.main()
