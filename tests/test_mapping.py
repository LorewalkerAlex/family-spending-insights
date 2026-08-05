from __future__ import annotations

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from family_spending.ingestion.cmb_email_transactions import CmbTransaction
from family_spending.mapping import (
    GENERAL_SHOPPING_CATEGORY,
    HIGH_VALUE_GENERAL_SHOPPING_REVIEW,
    MappingDataError,
    MappingResolutionError,
    OTHER_EXPENSE_REVIEW,
    UNCLASSIFIED_CATEGORY,
    load_merchant_mappings,
    resolve_transaction,
)

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
家居家电:
  - 测试家电
"""

BASE_MERCHANTS_WITH_APPLIANCE = BASE_MERCHANTS + """\
测试家电:
  - 支付宝-测试家电
"""

BASE_OVERRIDE = (
    '{"transaction_id":"cmb_override","category":"家居家电",'
    '"note":"单笔分类覆盖"}\n'
)


def make_transaction(
    *,
    transaction_id: str = "cmb_default",
    amount: str = "-10.00",
    description: str = "支付宝-测试餐饮",
) -> CmbTransaction:
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
        merchants: str = BASE_MERCHANTS_WITH_APPLIANCE,
        categories: str = BASE_CATEGORIES,
        overrides: str = BASE_OVERRIDE,
    ) -> tuple[Path, Path, Path]:
        merchants_path = root / "merchants.yaml"
        categories_path = root / "categories.yaml"
        overrides_path = root / "transaction_category_overrides.jsonl"
        merchants_path.write_text(merchants, encoding="utf-8")
        categories_path.write_text(categories, encoding="utf-8")
        overrides_path.write_text(overrides, encoding="utf-8")
        return merchants_path, categories_path, overrides_path

    def load_fixture(self, root: Path, **overrides: str):
        paths = self.write_mapping_files(root, **overrides)
        return load_merchant_mappings(*paths)


class MappingLoaderTests(MappingTestCase):
    def test_loads_reverse_indexes_and_override_categories(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            mappings = self.load_fixture(Path(temp_dir))

        self.assertEqual(mappings.description_to_merchant["支付宝-测试餐饮"], "测试餐饮")
        self.assertEqual(mappings.merchant_to_category["测试购物"], GENERAL_SHOPPING_CATEGORY)
        self.assertEqual(mappings.transaction_category_overrides["cmb_override"], "家居家电")
        self.assertNotIn(UNCLASSIFIED_CATEGORY, mappings.categories)

    def test_official_mapping_files_satisfy_contract(self) -> None:
        mappings = load_merchant_mappings()

        self.assertGreater(len(mappings.description_to_merchant), 0)
        self.assertGreater(len(mappings.merchant_to_category), 0)
        self.assertGreater(len(mappings.categories), 0)
        self.assertNotIn(UNCLASSIFIED_CATEGORY, mappings.categories)

    def test_duplicate_yaml_key_fails_with_file_and_value(self) -> None:
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
            root = Path(temp_dir)
            paths = self.write_mapping_files(root, merchants=merchants, categories=categories, overrides="")
            with self.assertRaisesRegex(MappingDataError, r"(?s)merchants\.yaml.*duplicate key '重复商户'"):
                load_merchant_mappings(*paths)

    def test_duplicate_description_across_merchants_fails(self) -> None:
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
            root = Path(temp_dir)
            paths = self.write_mapping_files(root, merchants=merchants, categories=categories, overrides="")
            with self.assertRaisesRegex(MappingDataError, "Duplicate description.*支付宝-重复描述"):
                load_merchant_mappings(*paths)

    def test_duplicate_merchant_across_categories_fails(self) -> None:
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
            root = Path(temp_dir)
            paths = self.write_mapping_files(root, merchants=merchants, categories=categories, overrides="")
            with self.assertRaisesRegex(MappingDataError, "Duplicate merchant.*重复商户"):
                load_merchant_mappings(*paths)

    def test_merchant_set_mismatch_reports_both_files(self) -> None:
        merchants = """\
仅商户文件:
  - 支付宝-仅商户文件
"""
        categories = """\
餐饮美食:
  - 仅分类文件
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = self.write_mapping_files(root, merchants=merchants, categories=categories, overrides="")
            with self.assertRaisesRegex(
                MappingDataError,
                r"merchants\.yaml.*categories\.yaml.*missing categories.*unknown merchants",
            ):
                load_merchant_mappings(*paths)

    def test_empty_description_list_fails(self) -> None:
        merchants = "空商户: []\n"
        categories = "其他支出:\n  - 空商户\n"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = self.write_mapping_files(root, merchants=merchants, categories=categories, overrides="")
            with self.assertRaisesRegex(MappingDataError, "non-empty description list"):
                load_merchant_mappings(*paths)

    def test_empty_merchant_name_fails(self) -> None:
        merchants = '"":\n  - 支付宝-空商户\n'
        categories = '其他支出:\n  - ""\n'
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = self.write_mapping_files(root, merchants=merchants, categories=categories, overrides="")
            with self.assertRaisesRegex(MappingDataError, "Invalid merchant name"):
                load_merchant_mappings(*paths)

    def test_empty_category_name_fails(self) -> None:
        merchants = "测试商户:\n  - 支付宝-测试商户\n"
        categories = '"":\n  - 测试商户\n'
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = self.write_mapping_files(root, merchants=merchants, categories=categories, overrides="")
            with self.assertRaisesRegex(MappingDataError, "Invalid category"):
                load_merchant_mappings(*paths)

    def test_invalid_yaml_reports_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = self.write_mapping_files(root, merchants="测试商户: [\n", overrides="")
            with self.assertRaisesRegex(MappingDataError, r"merchants\.yaml"):
                load_merchant_mappings(*paths)

    def test_runtime_unclassified_state_cannot_be_formal_category(self) -> None:
        merchants = "测试商户:\n  - 支付宝-测试商户\n"
        categories = "待分类:\n  - 测试商户\n"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = self.write_mapping_files(root, merchants=merchants, categories=categories, overrides="")
            with self.assertRaisesRegex(MappingDataError, "must not be defined as a formal category"):
                load_merchant_mappings(*paths)

    def test_duplicate_override_transaction_id_fails(self) -> None:
        overrides = BASE_OVERRIDE + BASE_OVERRIDE
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = self.write_mapping_files(root, overrides=overrides)
            with self.assertRaisesRegex(MappingDataError, "Duplicate override transaction_id.*cmb_override"):
                load_merchant_mappings(*paths)

    def test_override_unknown_category_fails(self) -> None:
        overrides = '{"transaction_id":"cmb_unknown","category":"不存在分类"}\n'
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = self.write_mapping_files(root, overrides=overrides)
            with self.assertRaisesRegex(MappingDataError, "references unknown category.*不存在分类"):
                load_merchant_mappings(*paths)

    def test_override_unknown_field_fails(self) -> None:
        overrides = '{"transaction_id":"cmb_unknown","category":"家居家电","memo":"typo"}\n'
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = self.write_mapping_files(root, overrides=overrides)
            with self.assertRaisesRegex(MappingDataError, "unknown fields.*memo"):
                load_merchant_mappings(*paths)

    def test_invalid_jsonl_reports_line_number(self) -> None:
        overrides = BASE_OVERRIDE + "not-json\n"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = self.write_mapping_files(root, overrides=overrides)
            with self.assertRaisesRegex(MappingDataError, r"line 2"):
                load_merchant_mappings(*paths)


class MappingResolutionTests(MappingTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.mappings = self.load_fixture(Path(self.temp_dir.name))

    def test_uses_merchant_default_category(self) -> None:
        result = resolve_transaction(make_transaction(), self.mappings)

        self.assertEqual(result.merchant_name, "测试餐饮")
        self.assertEqual(result.display_name, "测试餐饮")
        self.assertEqual(result.default_category, "餐饮美食")
        self.assertEqual(result.category, "餐饮美食")
        self.assertEqual(result.category_source, "merchant_default")
        self.assertFalse(result.is_unmatched)
        self.assertEqual(result.review_signals, ())

    def test_unmatched_description_keeps_original_display_name(self) -> None:
        transaction = make_transaction(description="支付宝-未知商户")
        result = resolve_transaction(transaction, self.mappings)

        self.assertIsNone(result.merchant_name)
        self.assertEqual(result.display_name, transaction.description)
        self.assertIsNone(result.default_category)
        self.assertEqual(result.category, UNCLASSIFIED_CATEGORY)
        self.assertEqual(result.category_source, "unclassified")
        self.assertTrue(result.is_unmatched)
        self.assertEqual(result.review_signals, ())

    def test_override_changes_only_final_category_and_suppresses_signals(self) -> None:
        transaction = make_transaction(
            transaction_id="cmb_override",
            amount="-2000",
            description="支付宝-测试购物",
        )
        result = resolve_transaction(transaction, self.mappings)

        self.assertEqual(result.merchant_name, "测试购物")
        self.assertEqual(result.display_name, "测试购物")
        self.assertEqual(result.default_category, GENERAL_SHOPPING_CATEGORY)
        self.assertEqual(result.category, "家居家电")
        self.assertEqual(result.category_source, "transaction_override")
        self.assertEqual(result.review_signals, ())

    def test_override_without_merchant_mapping_fails_consistently(self) -> None:
        transaction = make_transaction(
            transaction_id="cmb_override",
            description="支付宝-未知商户",
        )
        with self.assertRaisesRegex(
            MappingResolutionError,
            r"transaction_category_overrides\.jsonl.*merchants\.yaml.*cmb_override.*支付宝-未知商户.*家居家电",
        ):
            resolve_transaction(transaction, self.mappings)

    def test_other_expense_generates_non_blocking_review_signal(self) -> None:
        transaction = make_transaction(description="支付宝-测试其他")
        result = resolve_transaction(transaction, self.mappings)

        self.assertEqual(result.category, "其他支出")
        self.assertEqual(result.review_signals, (OTHER_EXPENSE_REVIEW,))

    def test_high_value_general_shopping_includes_negative_1000_boundary(self) -> None:
        for amount in ("-1000", "-1000.01"):
            with self.subTest(amount=amount):
                transaction = make_transaction(amount=amount, description="支付宝-测试购物")
                result = resolve_transaction(transaction, self.mappings)
                self.assertEqual(
                    result.review_signals,
                    (HIGH_VALUE_GENERAL_SHOPPING_REVIEW,),
                )

    def test_high_value_general_shopping_excludes_smaller_expense_and_positive_amount(self) -> None:
        for amount in ("-999.99", "1000", "2000"):
            with self.subTest(amount=amount):
                transaction = make_transaction(amount=amount, description="支付宝-测试购物")
                result = resolve_transaction(transaction, self.mappings)
                self.assertEqual(result.review_signals, ())


if __name__ == "__main__":
    unittest.main()
