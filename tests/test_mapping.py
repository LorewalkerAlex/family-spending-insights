from __future__ import annotations

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from family_spending.enrichment import (
    GENERAL_SHOPPING_CATEGORY,
    OTHER_EXPENSE_REVIEW,
    UNCLASSIFIED_CATEGORY,
)
from family_spending.ingestion.cmb_email_transactions import CmbTransaction
from family_spending.mapping import MappingDataError, load_merchant_mappings
from family_spending.transaction_resolution import build_cmb_domain_state

BASE_MERCHANTS = """\
测试餐饮:
  - 支付宝-测试餐饮
测试其他:
  - 支付宝-测试其他
测试购物:
  - 支付宝-测试购物
"""
BASE_CATEGORIES = """\
餐饮美食:
  - 测试餐饮
其他支出:
  - 测试其他
综合购物:
  - 测试购物
"""


def make_transaction(
    *,
    transaction_id: str = "cmb_default",
    amount: str = "10.00",
    description: str = "支付宝-测试餐饮",
) -> CmbTransaction:
    """Use raw CMB amount direction because Mapping resolves Source Records before refund-derived analytics."""
    return CmbTransaction(
        transaction_id=transaction_id,
        transaction_date=date(2026, 8, 1),
        amount=Decimal(amount),
        description=description,
        source_email="statement.eml",
        source_index=1,
    )


class MappingTestCase(unittest.TestCase):
    def write_mapping_files(
        self,
        root: Path,
        *,
        merchants: str = BASE_MERCHANTS,
        categories: str = BASE_CATEGORIES,
    ) -> tuple[Path, Path]:
        """Write the two reviewed Mapping inputs so every failure identifies its exact file boundary."""
        merchants_path = root / "merchants.yaml"
        categories_path = root / "categories.yaml"
        merchants_path.write_text(merchants, encoding="utf-8")
        categories_path.write_text(categories, encoding="utf-8")
        return merchants_path, categories_path

    def load_fixture(self, root: Path, **changes: str):
        """Reuse the production loader so resolution tests cannot bypass reviewed-file validation."""
        paths = self.write_mapping_files(root, **changes)
        return load_merchant_mappings(*paths)


class MappingLoaderTests(MappingTestCase):
    def test_loads_reverse_indexes_without_transaction_fact_inputs(self) -> None:
        """Mapping owns Merchant/default Category rules only; transaction-specific facts live in Enrichment state."""
        with tempfile.TemporaryDirectory() as temp_dir:
            mappings = self.load_fixture(Path(temp_dir))
        self.assertEqual(mappings.description_to_merchant["支付宝-测试餐饮"], "测试餐饮")
        self.assertEqual(mappings.merchant_to_category["测试购物"], GENERAL_SHOPPING_CATEGORY)
        self.assertNotIn(UNCLASSIFIED_CATEGORY, mappings.categories)
        self.assertEqual(
            set(type(mappings).__dataclass_fields__),
            {"description_to_merchant", "merchant_to_category", "categories", "merchants_path", "categories_path"},
        )

    def test_official_mapping_files_satisfy_contract(self) -> None:
        """Keep the real reviewed Mapping files under the same loader contract after retiring the historical transaction-specific Mapping input."""
        mappings = load_merchant_mappings()
        self.assertGreater(len(mappings.description_to_merchant), 0)
        self.assertGreater(len(mappings.merchant_to_category), 0)
        self.assertGreater(len(mappings.categories), 0)
        self.assertNotIn(UNCLASSIFIED_CATEGORY, mappings.categories)

    def test_duplicate_yaml_key_fails_with_file_and_value(self) -> None:
        """Duplicate reviewed keys must fail rather than silently selecting one authoritative rule."""
        merchants = """\
重复商户:
  - 支付宝-第一条
重复商户:
  - 支付宝-第二条
"""
        categories = """\
餐饮美食:
  - 重复商户
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = self.write_mapping_files(Path(temp_dir), merchants=merchants, categories=categories)
            with self.assertRaisesRegex(MappingDataError, r"(?s)merchants\.yaml.*duplicate key '重复商户'"):
                load_merchant_mappings(*paths)

    def test_duplicate_description_across_merchants_fails(self) -> None:
        """One description cannot imply two Merchants because refund evidence and Enrichment require deterministic identity."""
        merchants = """\
商户甲:
  - 支付宝-重复描述
商户乙:
  - 支付宝-重复描述
"""
        categories = """\
餐饮美食:
  - 商户甲
  - 商户乙
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = self.write_mapping_files(Path(temp_dir), merchants=merchants, categories=categories)
            with self.assertRaisesRegex(MappingDataError, "Duplicate description.*支付宝-重复描述"):
                load_merchant_mappings(*paths)

    def test_duplicate_merchant_across_categories_fails(self) -> None:
        """A Merchant keeps one default Category so current Enrichment remains unambiguous."""
        merchants = """\
重复商户:
  - 支付宝-重复商户
"""
        categories = """\
餐饮美食:
  - 重复商户
其他支出:
  - 重复商户
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = self.write_mapping_files(Path(temp_dir), merchants=merchants, categories=categories)
            with self.assertRaisesRegex(MappingDataError, "Duplicate merchant.*重复商户"):
                load_merchant_mappings(*paths)

    def test_merchant_set_mismatch_reports_both_files(self) -> None:
        """Both Mapping files must cover the same Merchant set so no default Category is implicit."""
        merchants = """\
仅商户文件:
  - 支付宝-仅商户文件
"""
        categories = """\
餐饮美食:
  - 仅分类文件
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = self.write_mapping_files(Path(temp_dir), merchants=merchants, categories=categories)
            with self.assertRaisesRegex(
                MappingDataError,
                r"merchants\.yaml.*categories\.yaml.*missing categories.*unknown merchants",
            ):
                load_merchant_mappings(*paths)

    def test_empty_description_list_fails(self) -> None:
        """A reviewed Merchant without any source description cannot participate in deterministic resolution."""
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = self.write_mapping_files(
                Path(temp_dir),
                merchants="空商户: []\n",
                categories="其他支出:\n  - 空商户\n",
            )
            with self.assertRaisesRegex(MappingDataError, "non-empty description list"):
                load_merchant_mappings(*paths)

    def test_empty_merchant_name_fails(self) -> None:
        """Blank Merchant identity would make both display and reconciliation evidence unusable."""
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = self.write_mapping_files(
                Path(temp_dir),
                merchants='"":\n  - 支付宝-空商户\n',
                categories='其他支出:\n  - ""\n',
            )
            with self.assertRaisesRegex(MappingDataError, "Invalid merchant name"):
                load_merchant_mappings(*paths)

    def test_empty_category_name_fails(self) -> None:
        """Blank Category names cannot be persisted as formal Enrichment state."""
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = self.write_mapping_files(
                Path(temp_dir),
                merchants="测试商户:\n  - 支付宝-测试商户\n",
                categories='"":\n  - 测试商户\n',
            )
            with self.assertRaisesRegex(MappingDataError, "Invalid category"):
                load_merchant_mappings(*paths)

    def test_invalid_yaml_reports_file(self) -> None:
        """Parsing errors must retain the reviewed input path so a failed rebuild is actionable."""
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = self.write_mapping_files(Path(temp_dir), merchants="测试商户: [\n")
            with self.assertRaisesRegex(MappingDataError, r"merchants\.yaml"):
                load_merchant_mappings(*paths)

    def test_runtime_unclassified_state_cannot_be_formal_category(self) -> None:
        """待分类 remains runtime state, not a reviewed Category that could hide unresolved source descriptions."""
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = self.write_mapping_files(
                Path(temp_dir),
                merchants="测试商户:\n  - 支付宝-测试商户\n",
                categories="待分类:\n  - 测试商户\n",
            )
            with self.assertRaisesRegex(MappingDataError, "must not be defined as a formal category"):
                load_merchant_mappings(*paths)


class MappingResolutionTests(MappingTestCase):
    def setUp(self) -> None:
        """Load reviewed Mapping files once because fresh Enrichment resolution now uses only Mapping defaults."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.mappings = self.load_fixture(Path(self.temp_dir.name))

    def resolve(self, transaction: CmbTransaction):
        """Run the complete CMB domain snapshot so tests cover the real SourceRecord-to-Enrichment path."""
        return build_cmb_domain_state((transaction,), self.mappings).enrichments[0]

    def test_uses_merchant_default_category(self) -> None:
        """Default Merchant Category remains the normal Enrichment initialization path."""
        result = self.resolve(make_transaction())
        self.assertEqual(result.merchant_name, "测试餐饮")
        self.assertEqual(result.display_name, "测试餐饮")
        self.assertEqual(result.default_category, "餐饮美食")
        self.assertEqual(result.category, "餐饮美食")
        self.assertEqual(result.category_source, "merchant_default")
        self.assertFalse(result.is_unclassified)
        self.assertEqual(result.review_signals, ())

    def test_unmatched_description_keeps_original_display_name(self) -> None:
        """Unclassified UI output still needs the source description even though it does not live on Transaction Core."""
        result = self.resolve(make_transaction(description="支付宝-未知商户"))
        self.assertIsNone(result.merchant_name)
        self.assertEqual(result.display_name, "支付宝-未知商户")
        self.assertIsNone(result.default_category)
        self.assertEqual(result.category, UNCLASSIFIED_CATEGORY)
        self.assertEqual(result.category_source, "unclassified")
        self.assertTrue(result.is_unclassified)
        self.assertEqual(result.review_signals, ())

    def test_other_expense_generates_non_blocking_review_signal(self) -> None:
        """Category-only review intent remains attached to Enrichment because it does not depend on net spending."""
        result = self.resolve(make_transaction(description="支付宝-测试其他"))
        self.assertEqual(result.category, "其他支出")
        self.assertEqual(result.review_signals, (OTHER_EXPENSE_REVIEW,))


if __name__ == "__main__":
    unittest.main()
