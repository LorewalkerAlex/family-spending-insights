from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from family_spending.feedback import (
    FeedbackContext,
    FeedbackError,
    create_feedback_item,
    read_feedback_items,
    update_feedback_status,
    write_feedback_items,
)


class FeedbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.path = Path(self.temp_dir.name) / "feedback.jsonl"

    def test_round_trip_and_reopen_preserve_capture_context(self) -> None:
        item = create_feedback_item(
            content="总览金额层级需要调整",
            context=FeedbackContext(
                runtime="desktop_web",
                page="/overview",
                workspace="overview",
            ),
        )
        write_feedback_items((item,), self.path)

        loaded = read_feedback_items(self.path)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].status, "open")
        self.assertEqual(loaded[0].context.runtime, "desktop_web")
        self.assertEqual(loaded[0].context.page, "/overview")

        resolved = update_feedback_status(loaded[0], "resolved")
        reopened = update_feedback_status(resolved, "open")
        self.assertEqual(reopened.created_at, item.created_at)
        self.assertEqual(reopened.context, item.context)
        self.assertEqual(reopened.status, "open")

    def test_missing_file_is_empty_and_duplicate_ids_are_rejected(self) -> None:
        self.assertEqual(read_feedback_items(self.path), ())
        item = create_feedback_item(
            content="测试反馈",
            context=FeedbackContext(runtime="mini_h5"),
        )
        with self.assertRaisesRegex(FeedbackError, "duplicate ids"):
            write_feedback_items((item, item), self.path)

    def test_corrupt_jsonl_is_rejected(self) -> None:
        self.path.write_text('{"id":"broken"}\nnot-json\n', encoding="utf-8")
        with self.assertRaises(FeedbackError):
            read_feedback_items(self.path)

    def test_entity_context_requires_type_and_id_together(self) -> None:
        with self.assertRaisesRegex(FeedbackError, "provided together"):
            create_feedback_item(
                content="测试反馈",
                context=FeedbackContext(entity_id="txn_123"),
            )


if __name__ == "__main__":
    unittest.main()
