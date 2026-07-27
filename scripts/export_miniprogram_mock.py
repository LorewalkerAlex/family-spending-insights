from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "miniprogram" / "mock-data" / "billing.js"
ENRICHED = ROOT / "mapping_output" / "2026-06" / "enriched_transactions.csv"
REVIEW = ROOT / "mapping_output" / "2026-06" / "review_required.csv"


def text(value: object) -> str:
    return "" if value is None else str(value).strip()


def stable_id(prefix: str, *parts: object) -> str:
    raw = "|".join(text(part) for part in parts)
    return f"{prefix}_{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:12]}"


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def transaction_payload(row: dict[str, str]) -> dict[str, object]:
    bank_amount = round(float(row["amount"]), 2)
    # The bank statement uses positive for charges and negative for credits in this sample.
    # The App ledger convention is the opposite: expenses negative, refunds positive.
    amount = -bank_amount
    merchant = text(row.get("canonical_merchant")) or text(row.get("suggested_merchant")) or text(row.get("source_merchant"))
    category = text(row.get("category")) or text(row.get("suggested_category")) or "待分类"
    return {
        "id": stable_id("tx", row.get("source_file"), row.get("csv_index")),
        "transactionDate": row["transaction_date"],
        "month": row["transaction_date"][:7],
        "merchant": merchant,
        "sourceMerchant": text(row.get("source_merchant")),
        "originalDescription": text(row.get("description")),
        "amount": amount,
        "bankAmount": bank_amount,
        "amountText": f"{'+' if amount > 0 else '-'}¥{abs(amount):,.2f}",
        "direction": "refund" if amount > 0 else "expense",
        "account": text(row.get("app_account")) or f"招商银行信用卡(尾号{row.get('card_last4', '')})",
        "cardLast4": text(row.get("card_last4")),
        "category": category,
        "categorySource": text(row.get("category_source")),
        "enrichmentStatus": text(row.get("enrichment_status")),
        "matchStatus": text(row.get("match_status")) or "unmatched",
        "matchScore": float(row["match_score"]) if text(row.get("match_score")) else None,
        "mappingId": text(row.get("mapping_id")),
        "sourceFile": text(row.get("source_file")),
    }


def review_payload(row: dict[str, str]) -> dict[str, object] | None:
    if text(row.get("status")) not in ("probable", "ambiguous", "unmatched_csv"):
        return None
    description = text(row.get("description"))
    if not description:
        return None
    bank_amount = round(float(text(row.get("csv_amount")) or "0"), 2)
    amount = -bank_amount
    return {
        "id": stable_id("review", row.get("source_file"), row.get("csv_index"), description),
        "status": text(row.get("status")),
        "transactionDate": text(row.get("transaction_date")),
        "sourceMerchant": text(row.get("source_merchant")),
        "description": description,
        "suggestedMerchant": text(row.get("canonical_merchant")),
        "suggestedCategory": text(row.get("category")) or "待分类",
        "account": text(row.get("app_account")) or f"招商银行信用卡(尾号{row.get('card_last4', '')})",
        "cardLast4": text(row.get("card_last4")),
        "amount": amount,
        "bankAmount": bank_amount,
        "amountText": f"{'+' if amount > 0 else '-'}¥{abs(amount):,.2f}",
        "score": float(row["match_score"]) if text(row.get("match_score")) else None,
    }


def build() -> dict[str, object]:
    transactions = [transaction_payload(row) for row in load_csv(ENRICHED)]
    transactions.sort(key=lambda row: (row["transactionDate"], row["id"]), reverse=True)
    reviews = [item for row in load_csv(REVIEW) if (item := review_payload(row))]
    reviews.sort(key=lambda row: (row["transactionDate"], row["id"]), reverse=True)

    summaries: dict[str, dict[str, object]] = {}
    by_month: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in transactions:
        by_month[str(row["month"])].append(row)
    for month, rows in by_month.items():
        expense = round(sum(abs(float(row["amount"])) for row in rows if float(row["amount"]) < 0), 2)
        refund = round(sum(float(row["amount"]) for row in rows if float(row["amount"]) > 0), 2)
        summaries[month] = {
            "month": month,
            "expense": expense,
            "refund": refund,
            "netExpense": round(expense - refund, 2),
            "transactionCount": len(rows),
            "pendingCount": sum(row["enrichmentStatus"] != "mapping_applied" for row in rows),
        }
    return {
        "generatedAt": "local-mock",
        "months": sorted(by_month, reverse=True),
        "transactions": transactions,
        "reviews": reviews,
        "summaries": summaries,
    }


if __name__ == "__main__":
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(build(), ensure_ascii=False, indent=2)
    OUTPUT.write_text(f"module.exports = {payload};\n", encoding="utf-8")
    print(f"Wrote {OUTPUT.relative_to(ROOT)}")
