import csv
import tempfile
import unittest
from pathlib import Path

from build_mapping import (
    AppTransaction,
    CSVTransaction,
    build_mapping,
    complete_transaction_date,
    extract_date,
    infer_category,
    load_review_confirmations,
    mapping_key,
    match_transactions,
    normalize_text,
    OCRItem,
    parse_amount,
)
from datetime import date


def csv_row(index: int, amount: float, merchant: str, mmdd: str = "0618") -> CSVTransaction:
    return CSVTransaction(
        csv_index=index, source_file="2026-07-10-test.csv", transaction_date=f"2026-{mmdd[:2]}-{mmdd[2:]}",
        transaction_mmdd=mmdd, post_mmdd=mmdd, description=f"支付宝-{merchant}", channel="支付宝",
        source_merchant=merchant, amount=amount, card_last4="4529", raw={},
    )


def app_row(index: int, amount: float, merchant: str, mmdd: str = "0618") -> AppTransaction:
    return AppTransaction(
        app_index=index, transaction_mmdd=mmdd, merchant=merchant, amount=amount,
        consumer="ignored", ocr_score=0.99, y=float(index * 200), raw_text=merchant,
    )


class ParsingTests(unittest.TestCase):
    def test_amount_and_date_do_not_conflict(self):
        self.assertEqual(parse_amount("-¥1.09"), -1.09)
        self.assertEqual(extract_date(OCRItem("-¥1.09", 1, 0, 0, 1, 1)), "")
        self.assertEqual(extract_date(OCRItem("06-29 18:49", 1, 0, 0, 1, 1)), "0629")

    def test_statement_year_rollover(self):
        self.assertEqual(complete_transaction_date("1231", date(2026, 1, 10)), "2025-12-31")
        self.assertEqual(complete_transaction_date("0102", date(2026, 1, 10)), "2026-01-02")

    def test_normalization_and_category(self):
        self.assertEqual(normalize_text("支付宝-上海盒马网络科技有限公司"), "盒马")
        self.assertEqual(infer_category("上海交通卡（复旦空中充）")[0], "交通出行")

    def test_review_confirmation_overrides_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "review.csv"
            fields = [
                "review_decision", "description", "source_channel", "card_last4",
                "canonical_merchant", "review_merchant", "app_account", "review_account",
                "category", "review_category", "transaction_date", "source_file", "csv_index", "csv_amount",
            ]
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerow({
                    "review_decision": "confirm", "description": "支付宝-测试主体",
                    "source_channel": "支付宝", "card_last4": "4529", "canonical_merchant": "旧值",
                    "review_merchant": "人工商户", "app_account": "默认账户", "review_account": "人工账户",
                    "category": "", "review_category": "人工分类", "transaction_date": "2026-06-20",
                    "source_file": "test.csv", "csv_index": "1", "csv_amount": "-10.00",
                })
            rows = load_review_confirmations(path)
            self.assertEqual(rows[0]["canonical_merchant"], "人工商户")
            self.assertEqual(rows[0]["category"], "人工分类")
            self.assertEqual(rows[0]["manually_confirmed"], "true")


class MatchingTests(unittest.TestCase):
    def test_duplicate_amounts_are_one_to_one(self):
        csv_rows = [csv_row(1, -10, "上海交通卡"), csv_row(2, -10, "瑞幸咖啡")]
        app_rows = [app_row(1, -10, "瑞幸咖啡"), app_row(2, -10, "上海交通卡")]
        matches, unmatched_csv, unmatched_app = match_transactions(csv_rows, app_rows, 0.84, 0.67)
        self.assertEqual(len(matches), 2)
        self.assertFalse(unmatched_csv)
        self.assertFalse(unmatched_app)
        pairs = {(row["source_merchant"], row["canonical_merchant"]) for row in matches}
        self.assertEqual(pairs, {("上海交通卡", "上海交通卡"), ("瑞幸咖啡", "瑞幸咖啡")})

    def test_cross_date_amounts_never_match(self):
        matches, unmatched_csv, unmatched_app = match_transactions(
            [csv_row(1, -100, "上海交通卡", "0611")],
            [app_row(1, -100, "滴滴出行", "0614")],
            0.84, 0.67,
        )
        self.assertFalse(matches)
        self.assertEqual(len(unmatched_csv), 1)
        self.assertEqual(len(unmatched_app), 1)

    def test_existing_mapping_promotes_known_alias(self):
        mapping = {
            "source_pattern": "支付宝-上海茵赫实业有限公司", "source_channel": "支付宝",
            "card_last4": "4529", "canonical_merchant": "Manner Coffee",
            "rule_status": "confirmed", "confirmed_count": "2",
        }
        matches, _, _ = match_transactions(
            [csv_row(1, -14.8, "上海茵赫实业有限公司", "0617")],
            [app_row(1, -14.8, "Manner Coffee", "0617")],
            0.84, 0.67, [mapping],
        )
        self.assertEqual(matches[0]["status"], "confirmed")
        self.assertIn("mapping=1.000", matches[0]["evidence"])

    def test_manual_mapping_is_not_overwritten(self):
        match = {
            "status": "confirmed", "description": "支付宝-盒马", "source_channel": "支付宝",
            "card_last4": "4529", "canonical_merchant": "盒马", "app_account": "card",
            "category": "食品杂货", "category_source": "keyword_rule", "transaction_date": "2026-06-01",
        }
        base = {
            "source_pattern": match["description"], "source_channel": "支付宝", "card_last4": "4529",
            "canonical_merchant": "人工盒马", "app_account": "manual", "category": "人工分类",
        }
        base["mapping_id"] = mapping_key(base)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mapping.csv"
            fields = list(base) + ["manually_confirmed"]
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerow({**base, "manually_confirmed": "true"})
            rows = build_mapping([match], path)
            self.assertEqual(rows[0]["canonical_merchant"], "人工盒马")

    def test_repeated_alias_is_promoted_by_consensus(self):
        evidence = []
        for index, day in enumerate(("0617", "0618"), 1):
            evidence.append({
                "status": "probable", "match_score": 0.72, "csv_index": index,
                "source_file": "2026-07-10-test.csv", "transaction_date": f"2026-06-{day[2:]}",
                "description": "支付宝-上海茵赫实业有限公司", "source_channel": "支付宝",
                "card_last4": "4529", "csv_amount": -14.8, "canonical_merchant": "Manner Coffee",
                "app_account": "招商银行信用卡(尾号4529)", "category": "餐饮",
                "category_source": "keyword_rule",
            })
        rows = build_mapping(evidence, None)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["confirmation_source"], "multi_sample_consensus")
        self.assertEqual(rows[0]["sample_count"], 2)


if __name__ == "__main__":
    unittest.main()
