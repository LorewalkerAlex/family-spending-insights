from __future__ import annotations

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from family_spending.enrichment import (
    HIGH_VALUE_GENERAL_SHOPPING_REVIEW,
    OTHER_EXPENSE_REVIEW,
)
from family_spending.ingestion.cmb_email_transactions import CmbTransaction, write_transactions_csv
from family_spending.mapping import load_merchant_mappings
from family_spending.transaction_resolution import (
    TransactionResolutionError,
    format_transaction_resolution_report,
    main,
    resolve_transactions,
    resolve_transactions_from_files,
)

MERCHANTS = """\
测试餐饮:
  - 支付宝-测试餐饮
测试其他:
  - 支付宝-测试其他
测试购物:
  - 支付宝-测试购物
"""
CATEGORIES_WITH_OVERRIDE = """\
餐饮美食:
  - 测试餐饮
其他支出:
  - 测试其他
综合购物:
  - 测试购物
家居家电:
  - 测试家电
"""
MERCHANTS_WITH_OVERRIDE = MERCHANTS + """\
测试家电:
  - 支付宝-测试家电
"""


def make_transaction(
    transaction_id: str,
    description: str,
    *,
    amount: str = "10.00",
    source_index: int = 1,
) -> CmbTransaction:
    """Use raw positive spending because the diagnostic entrypoint now owns refund-aware review evaluation."""
    return CmbTransaction(
        transaction_id=transaction_id,
        transaction_date=date(2026, 8, 1),
        amount=Decimal(amount),
        description=description,
        source_email="statement.eml",
        source_index=source_index,
    )


def write_mapping_files(
    root: Path,
    *,
    overrides: str = '{"transaction_id":"cmb_override","category":"家居家电","note":"单笔覆盖"}\n',
) -> tuple[Path, Path, Path]:
    """Keep file-based tests on the reviewed Mapping contract while allowing missing-override cases to vary independently."""
    merchants_path = root / "merchants.yaml"
    categories_path = root / "categories.yaml"
    overrides_path = root / "transaction_category_overrides.jsonl"
    merchants_path.write_text(MERCHANTS_WITH_OVERRIDE, encoding="utf-8")
    categories_path.write_text(CATEGORIES_WITH_OVERRIDE, encoding="utf-8")
    overrides_path.write_text(overrides, encoding="utf-8")
    return merchants_path, categories_path, overrides_path


class TransactionResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        """Load official-style files once because source-ID override binding is part of every diagnostic snapshot."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.mapping_paths = write_mapping_files(self.root)
        self.mappings = load_merchant_mappings(*self.mapping_paths)

    def test_batch_preserves_source_order_and_groups_results(self) -> None:
        """The new system IDs may differ, but source order, classification counts, and review groups must stay stable."""
        transactions = (
            make_transaction("cmb_default", "支付宝-测试餐饮", source_index=1),
            make_transaction("cmb_override", "支付宝-测试家电", source_index=2),
            make_transaction("cmb_unclassified", "支付宝-未知商户", source_index=3),
            make_transaction("cmb_other", "支付宝-测试其他", source_index=4),
            make_transaction("cmb_high_value", "支付宝-测试购物", amount="1000", source_index=5),
        )

        batch = resolve_transactions(transactions, self.mappings)

        self.assertEqual(
            tuple(item.source_record.id for item in batch.transactions),
            tuple(item.transaction_id for item in transactions),
        )
        self.assertTrue(all(item.transaction.id.startswith("txn_") for item in batch.transactions))
        self.assertEqual(batch.category_source_counts["merchant_default"], 3)
        self.assertEqual(batch.category_source_counts["transaction_override"], 1)
        self.assertEqual(batch.category_source_counts["unclassified"], 1)
        self.assertEqual(tuple(item.source_record.id for item in batch.unclassified), ("cmb_unclassified",))
        self.assertEqual(
            tuple(item.source_record.id for item in batch.reviews_by_signal[OTHER_EXPENSE_REVIEW]),
            ("cmb_other",),
        )
        self.assertEqual(
            tuple(item.source_record.id for item in batch.reviews_by_signal[HIGH_VALUE_GENERAL_SHOPPING_REVIEW]),
            ("cmb_high_value",),
        )

    def test_file_resolution_consumes_all_overrides_without_modifying_inputs(self) -> None:
        """The migration may reinterpret legacy IDs in memory but must never rewrite CSV or reviewed Mapping files."""
        transactions_path = self.root / "transactions.csv"
        transactions = (
            make_transaction("cmb_default", "支付宝-测试餐饮", source_index=1),
            make_transaction("cmb_override", "支付宝-测试家电", source_index=2),
        )
        write_transactions_csv(transactions, transactions_path)
        input_paths = (transactions_path, *self.mapping_paths)
        original_bytes = {path: path.read_bytes() for path in input_paths}

        batch = resolve_transactions_from_files(transactions_path, *self.mapping_paths)

        self.assertEqual(len(batch.transactions), 2)
        self.assertEqual(batch.category_source_counts["transaction_override"], 1)
        self.assertEqual({path: path.read_bytes() for path in input_paths}, original_bytes)

    def test_file_resolution_rejects_unconsumed_official_override(self) -> None:
        """A reviewed legacy override must still fail if its CMB source record is absent from the complete raw set."""
        transactions_path = self.root / "transactions.csv"
        write_transactions_csv(
            (make_transaction("cmb_default", "支付宝-测试餐饮"),),
            transactions_path,
        )
        with self.assertRaisesRegex(
            TransactionResolutionError,
            r"transaction_category_overrides\.jsonl.*cmb_override",
        ):
            resolve_transactions_from_files(transactions_path, *self.mapping_paths)

    def test_report_exposes_system_and_source_identity_only_for_actionable_details(self) -> None:
        """Reviewers need both IDs during migration, while non-actionable override rows should not clutter the report."""
        transactions = (
            make_transaction("cmb_override", "支付宝-测试家电", source_index=1),
            make_transaction("cmb_unclassified", "支付宝-未知商户", source_index=2),
            make_transaction("cmb_other", "支付宝-测试其他", source_index=3),
        )
        batch = resolve_transactions(transactions, self.mappings)

        report = format_transaction_resolution_report(batch)

        self.assertIn("Transactions: 3", report)
        self.assertIn("Transaction overrides: 1", report)
        self.assertIn("Unclassified: 1", report)
        self.assertIn(f"- {OTHER_EXPENSE_REVIEW}: 1", report)
        self.assertIn(f"- {HIGH_VALUE_GENERAL_SHOPPING_REVIEW}: 0", report)
        self.assertIn("source_record_id=cmb_unclassified", report)
        self.assertIn("source_record_id=cmb_other", report)
        self.assertIn("transaction_id=txn_", report)
        self.assertNotIn("source_record_id=cmb_override", report)

    def test_main_converts_known_errors_to_clean_cli_failure(self) -> None:
        """Keep the established CLI failure boundary so operators do not receive internal tracebacks for contract errors."""
        error = TransactionResolutionError("inconsistent official data")
        with patch(
            "family_spending.transaction_resolution.resolve_transactions_from_files",
            side_effect=error,
        ):
            with self.assertRaisesRegex(
                SystemExit,
                "Transaction resolution failed.*inconsistent official data",
            ):
                main()


if __name__ == "__main__":
    unittest.main()
