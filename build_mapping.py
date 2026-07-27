from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date
from difflib import SequenceMatcher
from itertools import permutations
from pathlib import Path
from typing import Any, Iterable

from PIL import Image


AMOUNT_RE = re.compile(r"(?P<sign>[-+−]?)\s*[¥￥]?\s*(?P<amount>\d[\d,]*\.\d{2})")
DATE_RE = re.compile(
    r"^\s*(?P<month>0?[1-9]|1[0-2])[-/.](?P<day>0?[1-9]|[12]\d|3[01])(?!\d)"
    r"(?:\s+\d{1,2}:\d{2})?"
)
CHANNELS = ("支付宝", "财付通", "微信支付", "云闪付")
COMPANY_WORDS = (
    "有限责任公司", "股份有限公司", "有限公司", "网络科技", "科技发展", "销售服务",
    "平台商户", "实业", "（中国）", "(中国)", "上海", "中国",
)
CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("交通出行", ("交通卡", "铁路", "12306", "滴滴", "航空", "地铁", "公交", "打车")),
    ("餐饮", ("餐饮", "咖啡", "茶姬", "饭店", "餐厅", "生蚝", "牛排", "popeyes", "新白鹿")),
    ("食品杂货", ("盒马", "aldi", "奥乐齐", "便利店", "逸刻", "超市")),
    ("网购", ("拼多多", "京东", "淘宝", "天猫", "旗舰店", "自营", "泡泡玛特", "无印良品", "muji")),
    ("生活缴费", ("生活缴费", "水费", "电费", "燃气", "话费", "宽带")),
    ("快递物流", ("顺丰", "货运", "快递", "物流")),
    ("汽车", ("蔚来", "汽车", "停车", "加油", "充电")),
    ("数码家电", ("苹果", "apple", "戴森", "美智光电", "绿联", "ugreen")),
    ("文娱", ("微梦创科", "微博", "广播电视", "电影", "演出")),
)


@dataclass
class OCRItem:
    text: str
    score: float
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def cx(self) -> float:
        return (self.x1 + self.x2) / 2

    @property
    def cy(self) -> float:
        return (self.y1 + self.y2) / 2


@dataclass
class AppTransaction:
    app_index: int
    transaction_mmdd: str
    merchant: str
    amount: float
    consumer: str
    ocr_score: float
    y: float
    raw_text: str


@dataclass
class CSVTransaction:
    csv_index: int
    source_file: str
    transaction_date: str
    transaction_mmdd: str
    post_mmdd: str
    description: str
    channel: str
    source_merchant: str
    amount: float
    card_last4: str
    raw: dict[str, str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Match App screenshot transactions with exported bank-bill CSV files."
    )
    parser.add_argument("--image", required=True, type=Path, help="App bill screenshot")
    parser.add_argument(
        "--csv", required=True, type=Path, nargs="+",
        help="One or more bank bill CSV files; natural months normally span two statements",
    )
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--mapping", type=Path, default=None, help="Existing mapping CSV to merge")
    parser.add_argument(
        "--review-input", type=Path, default=None,
        help="Optional reviewed CSV; rows with review_decision=confirm are promoted into mapping",
    )
    parser.add_argument("--ocr-cache", type=Path, default=None, help="Reuse an OCR JSON cache")
    parser.add_argument("--chunk-height", type=int, default=2400)
    parser.add_argument("--chunk-overlap", type=int, default=320)
    parser.add_argument("--confirmed-threshold", type=float, default=0.84)
    parser.add_argument("--probable-threshold", type=float, default=0.67)
    parser.add_argument("--force-ocr", action="store_true")
    parser.add_argument(
        "--all-csv-periods", action="store_true",
        help="Do not restrict CSV rows to the dominant month detected in the screenshot",
    )
    return parser.parse_args()


def safe_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def normalize_text(text: str) -> str:
    text = text.casefold()
    text = re.sub(r"[\s\-_—·•（）()【】\[\]<>《》/\\]+", "", text)
    text = text.replace("&", "and")
    for word in CHANNELS + COMPANY_WORDS:
        text = text.replace(normalize_literal(word), "")
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", text)


def normalize_literal(text: str) -> str:
    return re.sub(r"[\s\-_—·•（）()【】\[\]<>《》/\\]+", "", text.casefold())


def split_channel(description: str) -> tuple[str, str]:
    for channel in CHANNELS:
        prefix = channel + "-"
        if description.startswith(prefix):
            return channel, description[len(prefix):].strip()
    if "-" in description:
        head, tail = description.split("-", 1)
        if len(head) <= 8:
            return head.strip(), tail.strip()
    return "", description.strip()


def parse_amount(text: str) -> float | None:
    match = AMOUNT_RE.search(text.replace("O", "0"))
    if not match:
        return None
    value = float(match.group("amount").replace(",", ""))
    sign = match.group("sign")
    return -value if sign in ("-", "−") else value


def ocr_image(image_path: Path, chunk_height: int, overlap: int) -> list[OCRItem]:
    from rapidocr import RapidOCR

    engine = RapidOCR()
    image = Image.open(image_path).convert("RGB")
    width, height = image.size
    if chunk_height <= overlap:
        raise ValueError("chunk-height must be greater than chunk-overlap")

    items: list[OCRItem] = []
    step = chunk_height - overlap
    starts = list(range(0, height, step))
    for number, top in enumerate(starts, 1):
        bottom = min(height, top + chunk_height)
        crop = image.crop((0, top, width, bottom))
        result = engine(crop)
        if result.txts is not None:
            for box, text, score in zip(result.boxes, result.txts, result.scores, strict=True):
                xs = [float(point[0]) for point in box]
                ys = [float(point[1]) + top for point in box]
                items.append(OCRItem(safe_text(text), float(score), min(xs), min(ys), max(xs), max(ys)))
        print(f"OCR chunk {number}/{len(starts)}: y={top}:{bottom}", file=sys.stderr)
        if bottom == height:
            break

    return deduplicate_ocr(items)


def deduplicate_ocr(items: list[OCRItem]) -> list[OCRItem]:
    kept: list[OCRItem] = []
    for item in sorted(items, key=lambda value: (value.cy, value.cx, -value.score)):
        norm = normalize_literal(item.text)
        duplicate_index = None
        for idx in range(len(kept) - 1, max(-1, len(kept) - 50), -1):
            other = kept[idx]
            if item.cy - other.cy > 80:
                break
            if norm == normalize_literal(other.text) and abs(item.cx - other.cx) < 80 and abs(item.cy - other.cy) < 45:
                duplicate_index = idx
                break
        if duplicate_index is None:
            kept.append(item)
        elif item.score > kept[duplicate_index].score:
            kept[duplicate_index] = item
    return sorted(kept, key=lambda value: (value.cy, value.cx))


def dump_ocr(items: list[OCRItem], path: Path, image_path: Path) -> None:
    payload = {"image": str(image_path), "items": [asdict(item) for item in items]}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_ocr(path: Path) -> list[OCRItem]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [OCRItem(**item) for item in payload["items"]]


def extract_date(item: OCRItem) -> str:
    match = DATE_RE.search(item.text)
    if not match:
        return ""
    return f"{int(match.group('month')):02d}{int(match.group('day')):02d}"


def clean_merchant(text: str) -> str:
    text = AMOUNT_RE.sub("", text).strip(" -—·|：:")
    return re.sub(r"\s+", " ", text)


def parse_app_transactions(items: list[OCRItem], image_width: int) -> list[AppTransaction]:
    amount_items: list[tuple[OCRItem, float]] = []
    for item in items:
        amount = parse_amount(item.text)
        # Amounts live on the right side; allow full-width OCR lines containing merchant + amount.
        if amount is not None and (item.cx > image_width * 0.58 or item.x2 > image_width * 0.84):
            amount_items.append((item, amount))

    dates = [(item, extract_date(item)) for item in items if extract_date(item)]
    transactions: list[AppTransaction] = []
    seen: set[tuple[str, float, int]] = set()
    ignored = {"6月", "5月", "7月", "账单", "全部", "支出", "收入"}

    for amount_item, amount in amount_items:
        same_row = [
            item for item in items
            if abs(item.cy - amount_item.cy) <= 65 and item is not amount_item
        ]
        merchant_candidates = [
            item for item in same_row
            if item.cx > image_width * 0.05
            and not extract_date(item)
            and parse_amount(item.text) is None
            and item.text not in ignored
        ]

        inline_merchant = clean_merchant(amount_item.text)
        if inline_merchant and inline_merchant != amount_item.text and len(normalize_text(inline_merchant)) >= 2:
            merchant = inline_merchant
            merchant_score = amount_item.score
        elif merchant_candidates:
            merchant_item = max(merchant_candidates, key=lambda item: (len(normalize_text(item.text)), item.score))
            merchant = clean_merchant(merchant_item.text)
            merchant_score = merchant_item.score
        else:
            continue

        nearby_dates = [
            (abs(item.cy - amount_item.cy), item, mmdd)
            for item, mmdd in dates if abs(item.cy - amount_item.cy) <= 180
        ]
        if not nearby_dates:
            continue
        _, date_item, mmdd = min(nearby_dates, key=lambda value: value[0])

        below = [
            item for item in items
            if 15 < item.cy - amount_item.cy < 105
            and image_width * 0.28 < item.cx < image_width * 0.78
            and parse_amount(item.text) is None and not extract_date(item)
        ]
        consumer = max(below, key=lambda item: item.score).text if below else ""
        raw = " | ".join(item.text for item in sorted(same_row + [amount_item], key=lambda value: value.x1))
        key = (mmdd, round(amount, 2), round(amount_item.cy / 20))
        if key in seen:
            continue
        seen.add(key)
        transactions.append(AppTransaction(
            app_index=0,
            transaction_mmdd=mmdd,
            merchant=merchant,
            amount=round(amount, 2),
            consumer=consumer,
            ocr_score=round(min(amount_item.score, merchant_score, date_item.score), 5),
            y=round(amount_item.cy, 2),
            raw_text=raw,
        ))

    transactions.sort(key=lambda row: row.y)
    for idx, transaction in enumerate(transactions, 1):
        transaction.app_index = idx
    return transactions


def statement_date_from_name(path: Path) -> date:
    match = re.search(r"(20\d{2})-(\d{2})-(\d{2})", path.name)
    if not match:
        raise ValueError(f"Cannot infer statement date from CSV filename: {path.name}")
    return date(*(int(value) for value in match.groups()))


def complete_transaction_date(mmdd: str, statement_date: date) -> str:
    month, day = int(mmdd[:2]), int(mmdd[2:])
    year = statement_date.year - 1 if month > statement_date.month else statement_date.year
    return date(year, month, day).isoformat()


def load_csv_transactions(paths: Iterable[Path]) -> list[CSVTransaction]:
    transactions: list[CSVTransaction] = []
    index = 0
    for path in paths:
        statement_date = statement_date_from_name(path)
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                index += 1
                description = safe_text(row.get("description"))
                channel, merchant = split_channel(description)
                amount_text = safe_text(row.get("cny_amount") or row.get("raw_amount"))
                amount = round(float(amount_text), 2)
                mmdd = safe_text(row.get("transaction_mmdd")).zfill(4)
                transactions.append(CSVTransaction(
                    csv_index=index,
                    source_file=path.name,
                    transaction_date=complete_transaction_date(mmdd, statement_date),
                    transaction_mmdd=mmdd,
                    post_mmdd=safe_text(row.get("post_mmdd")).zfill(4),
                    description=description,
                    channel=channel,
                    source_merchant=merchant,
                    amount=amount,
                    card_last4=safe_text(row.get("card_last4")),
                    raw={key: safe_text(value) for key, value in row.items()},
                ))
    return transactions


def merchant_similarity(left: str, right: str) -> float:
    a, b = normalize_text(left), normalize_text(right)
    if not a or not b:
        return 0.0
    if a in b or b in a:
        containment = min(len(a), len(b)) / max(len(a), len(b))
        return min(1.0, 0.76 + containment * 0.24)
    ratio = SequenceMatcher(None, a, b).ratio()
    # Token-like character overlap helps bank legal entities match short App names.
    overlap = len(set(a) & set(b)) / max(1, len(set(a) | set(b)))
    return max(ratio, overlap * 0.9)


def infer_category(merchant: str) -> tuple[str, str]:
    normalized = normalize_literal(merchant)
    for category, keywords in CATEGORY_RULES:
        if any(normalize_literal(keyword) in normalized for keyword in keywords):
            return category, "keyword_rule"
    return "", "needs_review"


def account_name(card_last4: str) -> str:
    return f"招商银行信用卡(尾号{card_last4})" if card_last4 else "招商银行信用卡"


def pair_score(
    csv_row: CSVTransaction, app_row: AppTransaction, expected_rank_delta: float,
    known_mapping: dict[str, str] | None = None,
) -> tuple[float, str]:
    if abs(abs(csv_row.amount) - abs(app_row.amount)) > 0.011:
        return -1.0, "amount_mismatch"
    if csv_row.transaction_mmdd != app_row.transaction_mmdd:
        return -1.0, "date_mismatch"
    text_score = merchant_similarity(csv_row.source_merchant, app_row.merchant)
    order_score = max(0.0, 1.0 - abs(expected_rank_delta))
    mapped_score = 0.0
    if known_mapping:
        mapped_score = merchant_similarity(safe_text(known_mapping.get("canonical_merchant")), app_row.merchant)
    score = 0.64 + text_score * 0.22 + mapped_score * 0.22 + order_score * 0.04 + app_row.ocr_score * 0.02
    evidence = (
        f"amount=exact;text={text_score:.3f};date=1;mapping={mapped_score:.3f};"
        f"order={order_score:.3f};ocr={app_row.ocr_score:.3f}"
    )
    return min(score, 1.0), evidence


def best_group_assignment(
    csv_group: list[CSVTransaction], app_group: list[AppTransaction],
    csv_total: int, app_total: int, mappings: list[dict[str, str]],
) -> list[tuple[CSVTransaction, AppTransaction, float, str]]:
    if not csv_group or not app_group:
        return []
    # Amount groups are normally tiny. Exact enumeration gives stable global one-to-one results.
    if max(len(csv_group), len(app_group)) <= 8:
        if len(csv_group) <= len(app_group):
            left, right, swap = csv_group, app_group, False
        else:
            left, right, swap = app_group, csv_group, True
        best: tuple[float, list[tuple[CSVTransaction, AppTransaction, float, str]]] = (-math.inf, [])
        for chosen in permutations(right, len(left)):
            pairs = []
            total = 0.0
            for first, second in zip(left, chosen, strict=True):
                csv_row, app_row = (second, first) if swap else (first, second)
                rank_delta = csv_row.csv_index / max(csv_total, 1) - app_row.app_index / max(app_total, 1)
                score, evidence = pair_score(csv_row, app_row, rank_delta, mapping_for_csv(csv_row, mappings))
                total += score
                pairs.append((csv_row, app_row, score, evidence))
            if total > best[0]:
                best = total, pairs
        return best[1]

    candidates = []
    for csv_row in csv_group:
        for app_row in app_group:
            rank_delta = csv_row.csv_index / max(csv_total, 1) - app_row.app_index / max(app_total, 1)
            score, evidence = pair_score(csv_row, app_row, rank_delta, mapping_for_csv(csv_row, mappings))
            candidates.append((score, csv_row, app_row, evidence))
    used_csv: set[int] = set()
    used_app: set[int] = set()
    pairs = []
    for score, csv_row, app_row, evidence in sorted(candidates, key=lambda value: value[0], reverse=True):
        if csv_row.csv_index not in used_csv and app_row.app_index not in used_app:
            used_csv.add(csv_row.csv_index)
            used_app.add(app_row.app_index)
            pairs.append((csv_row, app_row, score, evidence))
    return pairs


def match_transactions(
    csv_rows: list[CSVTransaction], app_rows: list[AppTransaction],
    confirmed_threshold: float, probable_threshold: float, mappings: list[dict[str, str]] | None = None,
) -> tuple[list[dict[str, Any]], list[CSVTransaction], list[AppTransaction]]:
    mappings = mappings or []
    csv_by_amount: dict[tuple[str, float], list[CSVTransaction]] = defaultdict(list)
    app_by_amount: dict[tuple[str, float], list[AppTransaction]] = defaultdict(list)
    for row in csv_rows:
        csv_by_amount[(row.transaction_mmdd, round(abs(row.amount), 2))].append(row)
    for row in app_rows:
        app_by_amount[(row.transaction_mmdd, round(abs(row.amount), 2))].append(row)

    matches: list[dict[str, Any]] = []
    used_csv: set[int] = set()
    used_app: set[int] = set()
    for amount_key in sorted(set(csv_by_amount) & set(app_by_amount)):
        pairs = best_group_assignment(
            csv_by_amount[amount_key], app_by_amount[amount_key], len(csv_rows), len(app_rows), mappings
        )
        for csv_row, app_row, score, evidence in pairs:
            if csv_row.csv_index in used_csv or app_row.app_index in used_app:
                continue
            used_csv.add(csv_row.csv_index)
            used_app.add(app_row.app_index)
            if score >= confirmed_threshold:
                status = "confirmed"
            elif score >= probable_threshold:
                status = "probable"
            else:
                status = "ambiguous"
            category, category_source = infer_category(app_row.merchant)
            matches.append({
                "status": status,
                "match_score": round(score, 5),
                "evidence": evidence,
                "csv_index": csv_row.csv_index,
                "app_index": app_row.app_index,
                "transaction_date": csv_row.transaction_date,
                "transaction_mmdd": csv_row.transaction_mmdd,
                "source_file": csv_row.source_file,
                "description": csv_row.description,
                "source_channel": csv_row.channel,
                "source_merchant": csv_row.source_merchant,
                "card_last4": csv_row.card_last4,
                "csv_amount": csv_row.amount,
                "app_amount": app_row.amount,
                "canonical_merchant": app_row.merchant,
                "app_account": account_name(csv_row.card_last4),
                "category": category,
                "category_source": category_source,
                "consumer_ignored": app_row.consumer,
                "app_ocr_score": app_row.ocr_score,
                "app_raw_text": app_row.raw_text,
            })

    unmatched_csv = [row for row in csv_rows if row.csv_index not in used_csv]
    unmatched_app = [row for row in app_rows if row.app_index not in used_app]
    return sorted(matches, key=lambda row: row["csv_index"]), unmatched_csv, unmatched_app


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    if fieldnames is None:
        fieldnames = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def mapping_key(row: dict[str, Any]) -> str:
    raw = "|".join((
        normalize_text(safe_text(row.get("source_pattern"))),
        safe_text(row.get("source_channel")),
        safe_text(row.get("card_last4")),
    ))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def observation_id(row: dict[str, Any]) -> str:
    raw = "|".join((
        safe_text(row.get("source_file")), safe_text(row.get("csv_index")),
        safe_text(row.get("transaction_date")), safe_text(row.get("csv_amount")),
        safe_text(row.get("canonical_merchant")),
    ))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def load_mapping(path: Path | None) -> list[dict[str, str]]:
    if not path or not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def mapping_for_csv(row: CSVTransaction, mappings: list[dict[str, str]]) -> dict[str, str] | None:
    candidates = []
    source_norm = normalize_text(row.description)
    for mapping in mappings:
        if safe_text(mapping.get("rule_status")) not in ("", "confirmed"):
            continue
        pattern_norm = normalize_text(safe_text(mapping.get("source_pattern")))
        if not pattern_norm or pattern_norm != source_norm:
            continue
        channel = safe_text(mapping.get("source_channel"))
        card = safe_text(mapping.get("card_last4"))
        if channel and channel != row.channel:
            continue
        if card and card != row.card_last4:
            continue
        candidates.append(mapping)
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: (
            safe_text(item.get("manually_confirmed")).lower() in ("1", "true", "yes"),
            int(safe_text(item.get("confirmed_count")) or 0),
        ),
    )


def load_review_confirmations(path: Path | None) -> list[dict[str, Any]]:
    if not path or not path.exists():
        return []
    confirmed: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            decision = safe_text(row.get("review_decision")).lower()
            if decision not in ("confirm", "confirmed", "yes", "true", "确认"):
                continue
            if not safe_text(row.get("description")):
                continue
            item: dict[str, Any] = dict(row)
            item["status"] = "confirmed"
            item["canonical_merchant"] = safe_text(row.get("review_merchant")) or safe_text(row.get("canonical_merchant"))
            item["app_account"] = safe_text(row.get("review_account")) or safe_text(row.get("app_account"))
            item["category"] = safe_text(row.get("review_category")) or safe_text(row.get("category"))
            item["category_source"] = "manual_review"
            item["manually_confirmed"] = "true"
            confirmed.append(item)
    return confirmed


def build_mapping(
    matches: list[dict[str, Any]], existing_path: Path | None,
    review_confirmations: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    if existing_path and existing_path.exists():
        with existing_path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                mapping[safe_text(row.get("mapping_id")) or mapping_key(row)] = dict(row)

    all_evidence = matches + (review_confirmations or [])
    alias_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in matches:
        if item["status"] == "confirmed":
            continue
        alias_groups[mapping_key({
            "source_pattern": item["description"],
            "source_channel": item["source_channel"],
            "card_last4": item["card_last4"],
        })].append(item)
    consensus_keys = {
        key for key, group in alias_groups.items()
        if len(group) >= 2
        and len({normalize_text(item["canonical_merchant"]) for item in group}) == 1
    }

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for match in all_evidence:
        key = mapping_key({
            "source_pattern": match["description"],
            "source_channel": match["source_channel"],
            "card_last4": match["card_last4"],
        })
        is_manual = safe_text(match.get("manually_confirmed")).lower() in ("1", "true", "yes")
        if match["status"] != "confirmed" and key not in consensus_keys and not is_manual:
            continue
        evidence = dict(match)
        if is_manual:
            evidence["confirmation_source"] = "manual_review"
        elif key in consensus_keys and match["status"] != "confirmed":
            evidence["confirmation_source"] = "multi_sample_consensus"
        else:
            evidence["confirmation_source"] = "high_confidence_match"
        grouped[key].append(evidence)

    for key, group in grouped.items():
        representative = max(
            group,
            key=lambda item: (
                safe_text(item.get("manually_confirmed")).lower() in ("1", "true", "yes"),
                float(safe_text(item.get("match_score")) or 0),
            ),
        )
        base = {
            "source_pattern": representative["description"],
            "source_channel": representative["source_channel"],
            "card_last4": representative["card_last4"],
            "canonical_merchant": representative["canonical_merchant"],
            "app_account": representative["app_account"],
            "category": representative["category"],
            "category_source": representative["category_source"],
            "confirmation_source": representative["confirmation_source"],
        }
        existing = mapping.get(key)
        existing_manual = bool(existing) and safe_text(existing.get("manually_confirmed")).lower() in ("1", "true", "yes")
        group_manual = any(
            safe_text(item.get("manually_confirmed")).lower() in ("1", "true", "yes") for item in group
        )
        if existing and safe_text(existing.get("confirmation_source")) and not group_manual:
            base["confirmation_source"] = safe_text(existing.get("confirmation_source"))
        if existing_manual and not group_manual:
            base.update({
                field: safe_text(existing.get(field))
                for field in (
                    "canonical_merchant", "app_account", "category", "category_source", "confirmation_source"
                )
            })

        existing_ids = set(filter(None, safe_text(existing.get("observation_ids") if existing else "").split(";")))
        # Old automatically-generated rows had no observation IDs. Migrate them from current evidence
        # instead of counting the same run twice.
        ids = set(existing_ids)
        ids.update(observation_id(item) for item in group)
        dates = [safe_text(item.get("transaction_date")) for item in group if safe_text(item.get("transaction_date"))]
        if existing_ids and existing:
            dates.extend(filter(None, (safe_text(existing.get("first_seen")), safe_text(existing.get("last_seen")))))
        dates.sort()
        mapping[key] = {
            "mapping_id": key,
            **base,
            "sample_count": len(ids),
            "confirmed_count": len(ids),
            "first_seen": dates[0] if dates else "",
            "last_seen": dates[-1] if dates else "",
            "rule_status": "confirmed",
            "manually_confirmed": "true" if existing_manual or group_manual else "false",
            "observation_ids": ";".join(sorted(ids)),
        }
    return sorted(mapping.values(), key=lambda row: (safe_text(row.get("source_pattern")), safe_text(row.get("card_last4"))))


def build_enriched_transactions(
    csv_rows: list[CSVTransaction], matches: list[dict[str, Any]], mappings: list[dict[str, str]],
) -> list[dict[str, Any]]:
    matches_by_csv = {int(row["csv_index"]): row for row in matches}
    enriched: list[dict[str, Any]] = []
    for row in csv_rows:
        match = matches_by_csv.get(row.csv_index)
        known = mapping_for_csv(row, mappings)
        if known:
            merchant = safe_text(known.get("canonical_merchant"))
            account = safe_text(known.get("app_account")) or account_name(row.card_last4)
            category = safe_text(known.get("category"))
            category_source = safe_text(known.get("category_source"))
            status = "mapping_applied"
            mapping_id_value = safe_text(known.get("mapping_id")) or mapping_key(known)
        else:
            merchant = ""
            account = account_name(row.card_last4)
            category = ""
            category_source = "needs_review"
            status = "unmapped"
            mapping_id_value = ""
        enriched.append({
            "csv_index": row.csv_index,
            "source_file": row.source_file,
            "transaction_date": row.transaction_date,
            "post_mmdd": row.post_mmdd,
            "description": row.description,
            "source_channel": row.channel,
            "source_merchant": row.source_merchant,
            "amount": row.amount,
            "card_last4": row.card_last4,
            "canonical_merchant": merchant,
            "app_account": account,
            "category": category,
            "category_source": category_source,
            "enrichment_status": status,
            "mapping_id": mapping_id_value,
            "match_status": safe_text(match.get("status")) if match else "",
            "match_score": safe_text(match.get("match_score")) if match else "",
            "suggested_merchant": safe_text(match.get("canonical_merchant")) if match else "",
            "suggested_category": safe_text(match.get("category")) if match else "",
        })
    return enriched


def main() -> None:
    args = parse_args()
    if not args.image.exists():
        raise FileNotFoundError(args.image)
    for path in args.csv:
        if not path.exists():
            raise FileNotFoundError(path)

    period = args.image.stem
    output_dir = args.output_dir or Path("mapping_output") / period
    output_dir.mkdir(parents=True, exist_ok=True)
    ocr_path = args.ocr_cache or output_dir / "ocr_results.json"

    if ocr_path.exists() and not args.force_ocr:
        print(f"Using OCR cache: {ocr_path}", file=sys.stderr)
        ocr_items = load_ocr(ocr_path)
    else:
        ocr_items = ocr_image(args.image, args.chunk_height, args.chunk_overlap)
        dump_ocr(ocr_items, ocr_path, args.image)

    with Image.open(args.image) as image:
        image_width = image.width
    app_rows = parse_app_transactions(ocr_items, image_width)
    all_csv_rows = load_csv_transactions(args.csv)
    csv_rows = all_csv_rows
    dominant_month = ""
    screenshot_date_start = ""
    screenshot_date_end = ""
    if app_rows and not args.all_csv_periods:
        dominant_month = Counter(row.transaction_mmdd[:2] for row in app_rows).most_common(1)[0][0]
        month_dates = sorted(
            row.transaction_mmdd for row in app_rows if row.transaction_mmdd.startswith(dominant_month)
        )
        screenshot_date_start = month_dates[0]
        screenshot_date_end = month_dates[-1]
        filtered = [
            row for row in all_csv_rows
            if screenshot_date_start <= row.transaction_mmdd <= screenshot_date_end
        ]
        if filtered:
            csv_rows = filtered
    mapping_path = args.mapping or output_dir / "merchant_mapping.csv"
    existing_mappings = load_mapping(mapping_path)
    matches, unmatched_csv, unmatched_app = match_transactions(
        csv_rows, app_rows, args.confirmed_threshold, args.probable_threshold, existing_mappings
    )
    # Read manual decisions before writing a fresh review file. This also makes it safe
    # to pass the previous review_required.csv as --review-input.
    review_confirmations = load_review_confirmations(args.review_input)

    app_dicts = [asdict(row) for row in app_rows]
    write_csv(output_dir / "app_transactions.csv", app_dicts)
    write_csv(output_dir / "matched_transactions.csv", matches)

    review_rows: list[dict[str, Any]] = [row for row in matches if row["status"] != "confirmed"]
    review_rows.extend({
        "status": "unmatched_csv", "csv_index": row.csv_index,
        "transaction_date": row.transaction_date, "transaction_mmdd": row.transaction_mmdd,
        "source_file": row.source_file, "description": row.description,
        "source_channel": row.channel, "source_merchant": row.source_merchant,
        "card_last4": row.card_last4, "csv_amount": row.amount,
    } for row in unmatched_csv)
    review_rows.extend({
        "status": "unmatched_app", "app_index": row.app_index,
        "transaction_mmdd": row.transaction_mmdd, "app_amount": row.amount,
        "canonical_merchant": row.merchant, "consumer_ignored": row.consumer,
        "app_ocr_score": row.ocr_score, "app_raw_text": row.raw_text,
    } for row in unmatched_app)
    review_fields = [
        "status", "match_score", "evidence", "csv_index", "app_index", "transaction_date",
        "transaction_mmdd", "source_file", "description", "source_channel", "source_merchant",
        "card_last4", "csv_amount", "app_amount", "canonical_merchant", "app_account",
        "category", "category_source", "consumer_ignored", "app_ocr_score", "app_raw_text",
        "review_decision", "review_merchant", "review_account", "review_category", "review_note",
    ]
    write_csv(output_dir / "review_required.csv", review_rows, review_fields)

    mapping_rows = build_mapping(
        matches, mapping_path if mapping_path.exists() else None, review_confirmations
    )
    mapping_fields = [
        "mapping_id", "source_pattern", "source_channel", "card_last4", "canonical_merchant",
        "app_account", "category", "category_source", "sample_count", "confirmed_count", "first_seen", "last_seen",
        "rule_status", "manually_confirmed", "confirmation_source", "observation_ids",
    ]
    write_csv(mapping_path, mapping_rows, mapping_fields)
    enriched_rows = build_enriched_transactions(csv_rows, matches, mapping_rows)
    write_csv(output_dir / "enriched_transactions.csv", enriched_rows)

    status_counts: dict[str, int] = defaultdict(int)
    for row in matches:
        status_counts[row["status"]] += 1
    summary = {
        "image": str(args.image),
        "csv_files": [str(path) for path in args.csv],
        "ocr_items": len(ocr_items),
        "app_transactions": len(app_rows),
        "csv_transactions": len(csv_rows),
        "csv_transactions_before_month_filter": len(all_csv_rows),
        "detected_screenshot_month": dominant_month,
        "detected_screenshot_date_start": screenshot_date_start,
        "detected_screenshot_date_end": screenshot_date_end,
        "matches": len(matches),
        "match_status": dict(status_counts),
        "unmatched_csv": len(unmatched_csv),
        "unmatched_app": len(unmatched_app),
        "mapping_rules": len(mapping_rows),
        "review_confirmations_imported": len(review_confirmations),
        "enriched_transactions": len(enriched_rows),
        "notes": [
            "Consumer names are retained only in consumer_ignored for audit and never used for matching.",
            "Account names are derived from the bank CSV card suffix.",
            "Categories marked keyword_rule are initial local inferences; needs_review remains blank for manual enrichment.",
        ],
    }
    (output_dir / "run_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
