from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from statistics import mean
from typing import Any

from loguru import logger
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

SUPPORTED_SUFFIXES = {".jpg", ".jpeg", ".png"}
DEFAULT_SAMPLE_SIZE = 50
DEFAULT_CPU_THREADS = 4

# The row images are 1280px wide and about 222px high. These ratios leave
# enough margin around the visible text while excluding the icon and person name.
TOP_LINE_REGION = (0.10, 0.04, 0.995, 0.50)
AMOUNT_FALLBACK_REGION = (0.68, 0.04, 0.995, 0.52)
OCCURRED_AT_REGION = (0.72, 0.48, 0.995, 0.92)

TIME_CONTENT_THRESHOLD = 245
TIME_CONTENT_PADDING = 10
TIME_SCALE = 2
FIELD_CROP_PADDING = 12
LOW_SCORE_THRESHOLD = 0.90
TIME_SPLIT_MIN_GAP = 12
TIME_SPLIT_MIN_SIDE_WIDTH = 60
MERCHANT_AMOUNT_OVERLAP_RATIO = 0.20

AMOUNT_RE = re.compile(
    r"(?P<sign>[-+\u2212]?)\s*[\u00a5\uffe5]?\s*(?P<amount>\d[\d,]*\.\d{2})"
)
DATE_PATTERN = r"(?P<month>0?[1-9]|1[0-2])[-/.](?P<day>0?[1-9]|[12]\d|3[01])"
TIME_PATTERN = r"(?P<hour>[01]?\d|2[0-3]):(?P<minute>[0-5]\d)"
DATE_RE = re.compile(rf"^{DATE_PATTERN}$")
TIME_RE = re.compile(rf"^{TIME_PATTERN}$")
FULL_DATE_RE = re.compile(rf"^\s*{DATE_PATTERN}\s*$")
FULL_DATETIME_RE = re.compile(rf"^\s*{DATE_PATTERN}\s+{TIME_PATTERN}\s*$")
MERCHANT_TRUNCATED_RE = re.compile(r"(?:\.+|…+)$")


@dataclass(frozen=True)
class CropBox:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


@dataclass(frozen=True)
class OCRToken:
    text: str
    score: float
    x1: float
    y1: float
    x2: float
    y2: float


@dataclass(frozen=True)
class OCRResult:
    text: str
    score: float
    tokens: list[OCRToken]


@dataclass(frozen=True)
class AmountCandidate:
    text: str
    amount: str
    score: float
    x1: float
    y1: float
    x2: float
    y2: float
    start_index: int
    end_index: int


@dataclass(frozen=True)
class OccurredAtValue:
    occurred_at: str | None
    occurred_on: str | None
    precision: str | None
    correction: str | None = None


@dataclass(frozen=True)
class RowOCRResult:
    row_image: str
    merchant_text: str
    merchant_score: float
    amount_text: str
    amount: str | None
    amount_score: float
    amount_source: str | None
    occurred_at_text: str
    occurred_at_initial_text: str
    occurred_at: str | None
    occurred_on: str | None
    occurred_at_precision: str | None
    occurred_at_score: float
    top_line_text: str
    ocr_calls: int
    issues: list[str]
    notes: list[str]
    top_line_box: CropBox
    merchant_box: CropBox
    amount_box: CropBox
    occurred_at_box: CropBox
    occurred_at_content_box: CropBox
    top_line_tokens: list[OCRToken]
    merchant_tokens: list[OCRToken]
    amount_tokens: list[OCRToken]
    occurred_at_tokens: list[OCRToken]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run deterministic OCR inspection over split app transaction rows."
    )
    parser.add_argument("rows_dir", type=Path, help="Directory containing split transaction rows")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("tmp/app_row_ocr_inspection"),
        help="Output directory; deleted and rebuilt on every run",
    )
    parser.add_argument(
        "--cpu-threads",
        type=int,
        default=DEFAULT_CPU_THREADS,
        help=f"CPU threads used by ONNX Runtime; default: {DEFAULT_CPU_THREADS}",
    )
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--sample-size",
        type=int,
        default=DEFAULT_SAMPLE_SIZE,
        help=f"Evenly distributed rows to inspect; default: {DEFAULT_SAMPLE_SIZE}",
    )
    selection.add_argument("--all", action="store_true", help="Inspect every row image")
    selection.add_argument(
        "--only",
        nargs="+",
        metavar="ROW_IMAGE",
        help=(
            "Inspect named row images plus their immediate neighbors so image-order "
            "validation can run"
        ),
    )
    return parser.parse_args()


def configure_cpu_threads(cpu_threads: int) -> None:
    if cpu_threads <= 0:
        raise ValueError("cpu-threads must be greater than zero")

    thread_value = str(cpu_threads)
    for name in (
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
        "BLIS_NUM_THREADS",
    ):
        os.environ[name] = thread_value


def create_ocr_engine(cpu_threads: int) -> Any:
    configure_cpu_threads(cpu_threads)

    # Import after setting the environment variables so native runtimes see them.
    from rapidocr import RapidOCR

    return RapidOCR(
        params={
            "EngineConfig.onnxruntime.intra_op_num_threads": cpu_threads,
            "EngineConfig.onnxruntime.inter_op_num_threads": 1,
            "Global.log_level": "warning",
        }
    )


def find_row_images(rows_dir: Path) -> list[Path]:
    images = sorted(
        path
        for path in rows_dir.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    )
    if not images:
        supported = ", ".join(sorted(SUPPORTED_SUFFIXES))
        raise ValueError(f"No row images found in {rows_dir}; expected one of: {supported}")
    return images


def select_evenly(images: list[Path], sample_size: int) -> list[Path]:
    if sample_size <= 0:
        raise ValueError("sample-size must be greater than zero")
    if sample_size >= len(images):
        return images
    if sample_size == 1:
        return [images[len(images) // 2]]

    indexes = [round(index * (len(images) - 1) / (sample_size - 1)) for index in range(sample_size)]
    return [images[index] for index in indexes]


def row_source_stem(path_or_name: Path | str) -> str:
    stem = Path(path_or_name).stem
    if "__" not in stem:
        return stem
    return stem.rsplit("__", 1)[0]


def row_source_index(path_or_name: Path | str) -> int:
    stem = Path(path_or_name).stem
    if "__" not in stem:
        return 0
    suffix = stem.rsplit("__", 1)[1]
    return int(suffix) if suffix.isdigit() else 0


def select_named_with_neighbors(images: list[Path], requested_names: list[str]) -> list[Path]:
    by_name = {path.name: path for path in images}
    by_stem = {path.stem: path for path in images}
    source_groups: dict[str, list[Path]] = {}
    for path in images:
        source_groups.setdefault(row_source_stem(path), []).append(path)
    for group in source_groups.values():
        group.sort(key=row_source_index)

    requested: list[Path] = []
    missing: list[str] = []
    for name in requested_names:
        path = by_name.get(name) or by_stem.get(Path(name).stem)
        if path is None:
            missing.append(name)
        else:
            requested.append(path)
    if missing:
        raise ValueError(f"Requested row images not found: {', '.join(missing)}")

    selected: set[Path] = set()
    for path in requested:
        group = source_groups[row_source_stem(path)]
        index = group.index(path)
        for neighbor_index in range(max(0, index - 1), min(len(group), index + 2)):
            selected.add(group[neighbor_index])
    return sorted(selected)


def reset_output_dir(rows_dir: Path, output_dir: Path) -> None:
    rows_dir_resolved = rows_dir.resolve()
    output_dir_resolved = output_dir.resolve()
    current_dir_resolved = Path.cwd().resolve()

    if output_dir_resolved == rows_dir_resolved or output_dir_resolved in rows_dir_resolved.parents:
        raise ValueError(
            f"Refusing to delete output directory because it contains the row directory: {output_dir}"
        )
    if output_dir_resolved == current_dir_resolved or output_dir_resolved == Path(output_dir_resolved.anchor):
        raise ValueError(f"Refusing to delete unsafe output directory: {output_dir}")

    if output_dir.exists():
        shutil.rmtree(output_dir)

    for name in ("field_crops", "review_required"):
        (output_dir / name).mkdir(parents=True, exist_ok=True)


def ratio_box(image: Image.Image, region: tuple[float, float, float, float]) -> CropBox:
    left, top, right, bottom = region
    box = CropBox(
        left=round(image.width * left),
        top=round(image.height * top),
        right=round(image.width * right),
        bottom=round(image.height * bottom),
    )
    if box.width <= 0 or box.height <= 0:
        raise ValueError(f"Invalid crop box generated from region {region}: {box}")
    return box


def clamp_box(box: CropBox, image: Image.Image) -> CropBox:
    left = min(max(box.left, 0), image.width - 1)
    top = min(max(box.top, 0), image.height - 1)
    right = min(max(box.right, left + 1), image.width)
    bottom = min(max(box.bottom, top + 1), image.height)
    return CropBox(left, top, right, bottom)


def crop_image(image: Image.Image, box: CropBox) -> Image.Image:
    return image.crop((box.left, box.top, box.right, box.bottom))


def save_crop(image: Image.Image, row_path: Path, field_name: str, output_dir: Path) -> Path:
    output_path = output_dir / "field_crops" / f"{row_path.stem}__{field_name}.jpg"
    image.save(output_path, quality=95)
    return output_path


def join_token_texts(tokens: list[OCRToken]) -> str:
    if not tokens:
        return ""

    parts = [tokens[0].text]
    for token in tokens[1:]:
        previous = parts[-1]
        current = token.text
        if previous and current and previous[-1].isascii() and previous[-1].isalnum():
            if current[0].isascii() and current[0].isalnum():
                parts.append(" ")
        parts.append(current)
    return "".join(parts).strip()


def run_ocr(
    engine: Any,
    image: Image.Image,
    *,
    origin_x: float,
    origin_y: float,
    scale_x: float = 1.0,
    scale_y: float = 1.0,
) -> OCRResult:
    result = engine(image, use_cls=False)
    if result.txts is None or result.boxes is None or result.scores is None:
        return OCRResult(text="", score=0.0, tokens=[])

    tokens: list[OCRToken] = []
    for box, text, score in zip(result.boxes, result.txts, result.scores, strict=True):
        text_value = str(text).strip()
        if not text_value:
            continue

        xs = [float(point[0]) for point in box]
        ys = [float(point[1]) for point in box]
        tokens.append(
            OCRToken(
                text=text_value,
                score=float(score),
                x1=min(xs) / scale_x + origin_x,
                y1=min(ys) / scale_y + origin_y,
                x2=max(xs) / scale_x + origin_x,
                y2=max(ys) / scale_y + origin_y,
            )
        )

    tokens.sort(key=lambda token: (token.x1, token.y1))
    text = join_token_texts(tokens)
    score = min((token.score for token in tokens), default=0.0)
    return OCRResult(text=text, score=score, tokens=tokens)


def parse_amount(text: str) -> str | None:
    normalized = text.replace("O", "0").replace("o", "0")
    match = AMOUNT_RE.search(normalized)
    if not match:
        return None

    sign = "-" if match.group("sign") in {"-", "\u2212"} else ""
    raw_amount = match.group("amount").replace(",", "")
    try:
        amount = Decimal(f"{sign}{raw_amount}")
    except InvalidOperation:
        return None
    return f"{amount:.2f}"


def find_amount_candidate(tokens: list[OCRToken]) -> AmountCandidate | None:
    candidates: list[AmountCandidate] = []
    for start_index in range(len(tokens)):
        for end_index in range(start_index + 1, min(start_index + 3, len(tokens)) + 1):
            span = tokens[start_index:end_index]
            raw_text = "".join(token.text for token in span)
            parsed = parse_amount(raw_text)
            if parsed is None:
                continue

            candidates.append(
                AmountCandidate(
                    text=join_token_texts(span),
                    amount=parsed,
                    score=min(token.score for token in span),
                    x1=min(token.x1 for token in span),
                    y1=min(token.y1 for token in span),
                    x2=max(token.x2 for token in span),
                    y2=max(token.y2 for token in span),
                    start_index=start_index,
                    end_index=end_index,
                )
            )

    if not candidates:
        return None

    # Amount is the right-most decimal value on the top line.
    return max(candidates, key=lambda candidate: (candidate.x2, candidate.x1))


def validate_occurred_at(month: int, day: int, hour: int, minute: int) -> str | None:
    try:
        datetime(2000, month, day, hour, minute)
    except ValueError:
        return None
    return f"{month:02d}-{day:02d} {hour:02d}:{minute:02d}"


def validate_occurred_on(month: int, day: int) -> str | None:
    try:
        datetime(2000, month, day)
    except ValueError:
        return None
    return f"{month:02d}-{day:02d}"


def parse_occurred_at_text(text: str) -> OccurredAtValue:
    normalized = re.sub(r"\s+", " ", text.strip())
    datetime_match = FULL_DATETIME_RE.fullmatch(normalized)
    if datetime_match is not None:
        occurred_at = validate_occurred_at(
            int(datetime_match.group("month")),
            int(datetime_match.group("day")),
            int(datetime_match.group("hour")),
            int(datetime_match.group("minute")),
        )
        if occurred_at is not None:
            return OccurredAtValue(
                occurred_at=occurred_at,
                occurred_on=occurred_at[:5],
                precision="minute",
            )

    date_match = FULL_DATE_RE.fullmatch(normalized)
    if date_match is not None:
        occurred_on = validate_occurred_on(
            int(date_match.group("month")),
            int(date_match.group("day")),
        )
        if occurred_on is not None:
            return OccurredAtValue(
                occurred_at=None,
                occurred_on=occurred_on,
                precision="date",
            )

    return OccurredAtValue(None, None, None)


def parse_occurred_at_tokens(tokens: list[OCRToken]) -> OccurredAtValue:
    raw_text = join_token_texts(tokens)
    parsed = parse_occurred_at_text(raw_text)
    if parsed.precision is not None:
        return parsed

    date_token: OCRToken | None = None
    date_match: re.Match[str] | None = None
    for token in tokens:
        compact_date = re.sub(r"\s+", "", token.text)
        match = DATE_RE.fullmatch(compact_date)
        if match is not None:
            date_token = token
            date_match = match
            break

    if date_token is None or date_match is None:
        return OccurredAtValue(None, None, None)

    month = int(date_match.group("month"))
    day = int(date_match.group("day"))
    duplicated_digit = str(day)[-1]

    for token in tokens:
        if token is date_token or ":" not in token.text:
            continue

        compact = re.sub(r"[^0-9:]", "", token.text)
        if not re.fullmatch(r"\d{3}:\d{2}", compact):
            continue
        if compact[0] != duplicated_digit:
            continue
        if date_token.x2 <= token.x1:
            continue

        candidate_time = compact[1:]
        time_match = TIME_RE.fullmatch(candidate_time)
        if time_match is None:
            continue

        corrected = validate_occurred_at(
            month,
            day,
            int(time_match.group("hour")),
            int(time_match.group("minute")),
        )
        if corrected is not None:
            return OccurredAtValue(
                occurred_at=corrected,
                occurred_on=corrected[:5],
                precision="minute",
                correction="occurred_at_deduplicated_date_tail",
            )

    return OccurredAtValue(None, None, None)


def expand_local_bbox(
    bbox: tuple[int, int, int, int], image: Image.Image, padding: int
) -> CropBox:
    left, top, right, bottom = bbox
    return CropBox(
        left=max(0, left - padding),
        top=max(0, top - padding),
        right=min(image.width, right + padding),
        bottom=min(image.height, bottom + padding),
    )


def prepare_occurred_at_crop(
    image: Image.Image,
) -> tuple[Image.Image, Image.Image, CropBox, CropBox]:
    region_box = ratio_box(image, OCCURRED_AT_REGION)
    original_crop = crop_image(image, region_box)
    gray = original_crop.convert("L")

    # Use a smoothed mask only to find the gray text. OCR still receives a
    # grayscale image with autocontrast rather than a hard binary threshold.
    mask_source = gray.filter(ImageFilter.MedianFilter(size=3))
    mask = mask_source.point(lambda value: 255 if value < TIME_CONTENT_THRESHOLD else 0)
    content_bbox = mask.getbbox()
    if content_bbox is None:
        local_content_box = CropBox(0, 0, gray.width, gray.height)
    else:
        local_content_box = expand_local_bbox(content_bbox, gray, TIME_CONTENT_PADDING)

    content = crop_image(gray, local_content_box)
    enhanced = ImageOps.autocontrast(content, cutoff=1)
    enhanced = ImageEnhance.Contrast(enhanced).enhance(1.35)
    enhanced = enhanced.resize(
        (enhanced.width * TIME_SCALE, enhanced.height * TIME_SCALE),
        Image.Resampling.LANCZOS,
    ).convert("RGB")

    row_content_box = CropBox(
        left=region_box.left + local_content_box.left,
        top=region_box.top + local_content_box.top,
        right=region_box.left + local_content_box.right,
        bottom=region_box.top + local_content_box.bottom,
    )
    return original_crop, enhanced, region_box, row_content_box


def find_occurred_at_split_x(image: Image.Image) -> int | None:
    gray = image.convert("L")
    min_dark_pixels = max(2, round(gray.height * 0.03))
    active_columns: list[int] = []
    pixels = gray.load()
    for x in range(gray.width):
        dark_pixels = sum(1 for y in range(gray.height) if pixels[x, y] < TIME_CONTENT_THRESHOLD)
        if dark_pixels >= min_dark_pixels:
            active_columns.append(x)

    if len(active_columns) < 2:
        return None

    content_left = active_columns[0]
    content_right = active_columns[-1]
    candidates: list[tuple[int, int]] = []
    for left, right in zip(active_columns, active_columns[1:], strict=False):
        gap_width = right - left - 1
        if gap_width < TIME_SPLIT_MIN_GAP:
            continue
        if left - content_left + 1 < TIME_SPLIT_MIN_SIDE_WIDTH:
            continue
        if content_right - right + 1 < TIME_SPLIT_MIN_SIDE_WIDTH:
            continue
        candidates.append((gap_width, (left + right) // 2))

    if not candidates:
        return None
    return max(candidates)[1]


def parse_date_component(text: str) -> tuple[int, int] | None:
    compact = re.sub(r"\s+", "", text)
    match = DATE_RE.fullmatch(compact)
    if match is None:
        return None
    month = int(match.group("month"))
    day = int(match.group("day"))
    if validate_occurred_on(month, day) is None:
        return None
    return month, day


def parse_time_component(text: str) -> tuple[int, int] | None:
    compact = re.sub(r"\s+", "", text)
    match = TIME_RE.fullmatch(compact)
    if match is None:
        return None
    return int(match.group("hour")), int(match.group("minute"))


def run_split_occurred_at_ocr(
    engine: Any,
    enhanced: Image.Image,
    content_box: CropBox,
    row_path: Path,
    output_dir: Path,
) -> tuple[OCRResult | None, OccurredAtValue, int]:
    split_x = find_occurred_at_split_x(enhanced)
    if split_x is None:
        return None, OccurredAtValue(None, None, None), 0

    date_crop = enhanced.crop((0, 0, split_x, enhanced.height))
    time_crop = enhanced.crop((split_x, 0, enhanced.width, enhanced.height))
    save_crop(date_crop, row_path, "occurred_at_date", output_dir)
    save_crop(time_crop, row_path, "occurred_at_time", output_dir)

    date_result = run_ocr(
        engine,
        date_crop,
        origin_x=content_box.left,
        origin_y=content_box.top,
        scale_x=TIME_SCALE,
        scale_y=TIME_SCALE,
    )
    time_result = run_ocr(
        engine,
        time_crop,
        origin_x=content_box.left + split_x / TIME_SCALE,
        origin_y=content_box.top,
        scale_x=TIME_SCALE,
        scale_y=TIME_SCALE,
    )

    date_value = parse_date_component(date_result.text)
    time_value = parse_time_component(time_result.text)
    if date_value is None or time_value is None:
        return None, OccurredAtValue(None, None, None), 2

    occurred_at = validate_occurred_at(*date_value, *time_value)
    if occurred_at is None:
        return None, OccurredAtValue(None, None, None), 2

    tokens = sorted(date_result.tokens + time_result.tokens, key=lambda token: (token.x1, token.y1))
    combined = OCRResult(
        text=f"{date_result.text.strip()} {time_result.text.strip()}",
        score=min(date_result.score, time_result.score),
        tokens=tokens,
    )
    return (
        combined,
        OccurredAtValue(
            occurred_at=occurred_at,
            occurred_on=occurred_at[:5],
            precision="minute",
            correction="occurred_at_split_ocr_used",
        ),
        2,
    )


def tokens_before_amount(
    top_tokens: list[OCRToken], top_amount: AmountCandidate | None, amount_x1: float | None
) -> list[OCRToken]:
    if top_amount is not None:
        return top_tokens[: top_amount.start_index]
    if amount_x1 is not None:
        return [token for token in top_tokens if token.x2 < amount_x1 - 2]
    return top_tokens


def dynamic_field_boxes(
    image: Image.Image,
    top_line_box: CropBox,
    merchant_tokens: list[OCRToken],
    amount_candidate: AmountCandidate | None,
) -> tuple[CropBox, CropBox]:
    if amount_candidate is not None:
        merchant_right = round(amount_candidate.x1) - FIELD_CROP_PADDING
        amount_left = round(amount_candidate.x1) - FIELD_CROP_PADDING
        amount_right = round(amount_candidate.x2) + FIELD_CROP_PADDING
    else:
        merchant_right = round(image.width * AMOUNT_FALLBACK_REGION[0])
        amount_left = merchant_right
        amount_right = image.width

    if merchant_tokens:
        merchant_left = max(
            top_line_box.left,
            round(min(token.x1 for token in merchant_tokens)) - FIELD_CROP_PADDING,
        )
    else:
        merchant_left = top_line_box.left

    merchant_box = clamp_box(
        CropBox(merchant_left, top_line_box.top, merchant_right, top_line_box.bottom), image
    )
    amount_box = clamp_box(
        CropBox(amount_left, top_line_box.top, amount_right, top_line_box.bottom), image
    )
    return merchant_box, amount_box


def inspect_row(engine: Any, row_path: Path, output_dir: Path) -> RowOCRResult:
    with Image.open(row_path) as source:
        image = source.convert("RGB")

    top_line_box = ratio_box(image, TOP_LINE_REGION)
    top_line_crop = crop_image(image, top_line_box)
    save_crop(top_line_crop, row_path, "top_line", output_dir)
    top_line = run_ocr(
        engine,
        top_line_crop,
        origin_x=top_line_box.left,
        origin_y=top_line_box.top,
    )
    ocr_calls = 1

    top_amount = find_amount_candidate(top_line.tokens)
    amount_candidate = top_amount
    amount_source: str | None = "top_line" if top_amount is not None else None
    amount_tokens = (
        top_line.tokens[top_amount.start_index : top_amount.end_index]
        if top_amount is not None
        else []
    )

    notes: list[str] = []
    if amount_candidate is None:
        fallback_box = ratio_box(image, AMOUNT_FALLBACK_REGION)
        fallback_crop = crop_image(image, fallback_box)
        fallback = run_ocr(
            engine,
            fallback_crop,
            origin_x=fallback_box.left,
            origin_y=fallback_box.top,
        )
        ocr_calls += 1
        amount_candidate = find_amount_candidate(fallback.tokens)
        if amount_candidate is not None:
            amount_source = "fallback"
            amount_tokens = fallback.tokens[
                amount_candidate.start_index : amount_candidate.end_index
            ]
            notes.append("amount_fallback_used")

    amount_x1 = amount_candidate.x1 if amount_candidate is not None else None
    merchant_tokens = tokens_before_amount(top_line.tokens, top_amount, amount_x1)
    merchant_text = join_token_texts(merchant_tokens)
    merchant_score = min((token.score for token in merchant_tokens), default=0.0)

    merchant_box, amount_box = dynamic_field_boxes(
        image, top_line_box, merchant_tokens, amount_candidate
    )
    save_crop(crop_image(image, merchant_box), row_path, "merchant", output_dir)
    save_crop(crop_image(image, amount_box), row_path, "amount", output_dir)

    time_original, time_enhanced, time_box, time_content_box = prepare_occurred_at_crop(image)
    save_crop(time_original, row_path, "occurred_at_original", output_dir)
    save_crop(time_enhanced, row_path, "occurred_at_enhanced", output_dir)
    occurred_at_result = run_ocr(
        engine,
        time_enhanced,
        origin_x=time_content_box.left,
        origin_y=time_content_box.top,
        scale_x=TIME_SCALE,
        scale_y=TIME_SCALE,
    )
    ocr_calls += 1
    occurred_at_initial_text = occurred_at_result.text
    occurred_at_value = parse_occurred_at_tokens(occurred_at_result.tokens)
    if occurred_at_value.correction is not None:
        notes.append(occurred_at_value.correction)

    if occurred_at_value.precision is None:
        original_time_result = run_ocr(
            engine,
            time_original,
            origin_x=time_box.left,
            origin_y=time_box.top,
        )
        ocr_calls += 1
        original_value = parse_occurred_at_tokens(original_time_result.tokens)
        if original_value.precision is not None:
            occurred_at_result = original_time_result
            occurred_at_value = original_value
            notes.append("occurred_at_original_fallback_used")
            if original_value.correction is not None:
                notes.append(original_value.correction)

    if occurred_at_value.precision is None:
        split_result, split_value, split_calls = run_split_occurred_at_ocr(
            engine,
            time_enhanced,
            time_content_box,
            row_path,
            output_dir,
        )
        ocr_calls += split_calls
        if split_result is not None and split_value.precision is not None:
            occurred_at_result = split_result
            occurred_at_value = split_value
            if split_value.correction is not None:
                notes.append(split_value.correction)

    amount_text = amount_candidate.text if amount_candidate is not None else ""
    parsed_amount = amount_candidate.amount if amount_candidate is not None else None
    amount_score = amount_candidate.score if amount_candidate is not None else 0.0

    issues: list[str] = []
    if not merchant_text:
        issues.append("merchant_missing")
    if parsed_amount is None:
        issues.append("amount_unparsed")
    if occurred_at_value.precision is None:
        issues.append("occurred_at_unparsed")
    if MERCHANT_TRUNCATED_RE.search(merchant_text.rstrip()):
        issues.append("merchant_truncated_in_app")
    if merchant_score and merchant_score < LOW_SCORE_THRESHOLD:
        issues.append("merchant_low_score")
    if amount_score and amount_score < LOW_SCORE_THRESHOLD:
        issues.append("amount_low_score")
    if occurred_at_result.score and occurred_at_result.score < LOW_SCORE_THRESHOLD:
        issues.append("occurred_at_low_score")
    if merchant_tokens and amount_candidate is not None:
        merchant_left = min(token.x1 for token in merchant_tokens)
        merchant_right = max(token.x2 for token in merchant_tokens)
        overlap_width = max(0.0, merchant_right - amount_candidate.x1)
        smaller_width = min(
            merchant_right - merchant_left,
            amount_candidate.x2 - amount_candidate.x1,
        )
        overlap_ratio = overlap_width / smaller_width if smaller_width > 0 else 0.0
        if overlap_ratio >= MERCHANT_AMOUNT_OVERLAP_RATIO:
            issues.append("merchant_amount_overlap")

    return RowOCRResult(
        row_image=row_path.name,
        merchant_text=merchant_text,
        merchant_score=merchant_score,
        amount_text=amount_text,
        amount=parsed_amount,
        amount_score=amount_score,
        amount_source=amount_source,
        occurred_at_text=occurred_at_result.text,
        occurred_at_initial_text=occurred_at_initial_text,
        occurred_at=occurred_at_value.occurred_at,
        occurred_on=occurred_at_value.occurred_on,
        occurred_at_precision=occurred_at_value.precision,
        occurred_at_score=occurred_at_result.score,
        top_line_text=top_line.text,
        ocr_calls=ocr_calls,
        issues=issues,
        notes=notes,
        top_line_box=top_line_box,
        merchant_box=merchant_box,
        amount_box=amount_box,
        occurred_at_box=time_box,
        occurred_at_content_box=time_content_box,
        top_line_tokens=top_line.tokens,
        merchant_tokens=merchant_tokens,
        amount_tokens=amount_tokens,
        occurred_at_tokens=occurred_at_result.tokens,
    )


def add_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def occurred_at_order_value(result: RowOCRResult) -> datetime | None:
    if result.occurred_at_precision != "minute" or result.occurred_at is None:
        return None

    # Use a fixed leap year so MM-DD parsing is deterministic and supports Feb 29.
    return datetime.strptime(
        f"2000-{result.occurred_at}",
        "%Y-%m-%d %H:%M",
    )


def add_order_validation_issues(results: list[RowOCRResult]) -> None:
    groups: dict[str, list[RowOCRResult]] = {}
    for result in results:
        groups.setdefault(row_source_stem(result.row_image), []).append(result)

    for group in groups.values():
        group.sort(key=lambda result: row_source_index(result.row_image))
        flagged: set[str] = set()

        for index in range(1, len(group) - 1):
            previous_value = occurred_at_order_value(group[index - 1])
            current_value = occurred_at_order_value(group[index])
            next_value = occurred_at_order_value(group[index + 1])
            if previous_value is None or current_value is None or next_value is None:
                continue
            if previous_value >= next_value and not previous_value >= current_value >= next_value:
                flagged.add(group[index].row_image)

        for index in range(1, len(group)):
            previous = group[index - 1]
            current = group[index]
            previous_value = occurred_at_order_value(previous)
            current_value = occurred_at_order_value(current)
            if previous_value is None or current_value is None or current_value <= previous_value:
                continue
            if previous.row_image not in flagged and current.row_image not in flagged:
                flagged.add(previous.row_image)
                flagged.add(current.row_image)

        for result in group:
            if result.row_image in flagged:
                add_unique(result.issues, "occurred_at_order_violation")


def order_context_paths(row_path: Path, all_images: list[Path]) -> list[Path]:
    group = sorted(
        (path for path in all_images if row_source_stem(path) == row_source_stem(row_path)),
        key=row_source_index,
    )
    index = group.index(row_path)
    return group[max(0, index - 1) : min(len(group), index + 2)]


def write_review_bundle(
    result: RowOCRResult,
    row_path: Path,
    output_dir: Path,
    all_images: list[Path],
) -> None:
    bundle_dir = output_dir / "review_required" / row_path.stem
    bundle_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(row_path, bundle_dir / f"{row_path.stem}__row{row_path.suffix.lower()}")

    for field_path in sorted((output_dir / "field_crops").glob(f"{row_path.stem}__*.jpg")):
        shutil.copy2(field_path, bundle_dir / field_path.name)

    context_names: list[str] = []
    if "occurred_at_order_violation" in result.issues:
        context_dir = bundle_dir / "order_context"
        context_dir.mkdir(exist_ok=True)
        for context_path in order_context_paths(row_path, all_images):
            destination = context_dir / context_path.name
            shutil.copy2(context_path, destination)
            context_names.append(context_path.name)

    (bundle_dir / "details.json").write_text(
        json.dumps(asdict(result), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    parsed_value = result.occurred_at or result.occurred_on or ""
    detail_lines = [
        f"# {result.row_image}",
        "",
        f"- Merchant: `{result.merchant_text}`",
        f"- Amount: `{result.amount or result.amount_text}`",
        f"- Occurred-at initial OCR: `{result.occurred_at_initial_text}`",
        f"- Occurred-at selected OCR: `{result.occurred_at_text}`",
        f"- Occurred-at parsed: `{parsed_value}`",
        f"- Occurred-at precision: `{result.occurred_at_precision or ''}`",
        f"- Issues: {', '.join(result.issues)}",
        f"- Notes: {', '.join(result.notes) or 'none'}",
    ]
    if context_names:
        detail_lines.append(f"- Order context: {', '.join(context_names)}")
    detail_lines.append("")
    (bundle_dir / "details.md").write_text(
        "\n".join(detail_lines),
        encoding="utf-8",
    )


def write_review_bundles(
    results: list[RowOCRResult],
    selected_images: list[Path],
    all_images: list[Path],
    output_dir: Path,
) -> None:
    selected_by_name = {path.name: path for path in selected_images}
    for result in results:
        if not result.issues:
            continue
        write_review_bundle(result, selected_by_name[result.row_image], output_dir, all_images)


def write_jsonl(results: list[RowOCRResult], output_path: Path) -> None:
    with output_path.open("w", encoding="utf-8") as handle:
        for result in results:
            handle.write(json.dumps(asdict(result), ensure_ascii=False) + "\n")


def markdown_cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def write_review(
    results: list[RowOCRResult], total_rows: int, cpu_threads: int, output_path: Path
) -> None:
    merchant_scores = [result.merchant_score for result in results if result.merchant_text]
    amount_scores = [result.amount_score for result in results if result.amount_text]
    occurred_at_scores = [
        result.occurred_at_score for result in results if result.occurred_at_text
    ]
    review_count = sum(bool(result.issues) for result in results)
    fallback_count = sum(result.amount_source == "fallback" for result in results)
    date_only_count = sum(result.occurred_at_precision == "date" for result in results)
    split_ocr_count = sum("occurred_at_split_ocr_used" in result.notes for result in results)
    order_violation_count = sum(
        "occurred_at_order_violation" in result.issues for result in results
    )
    total_ocr_calls = sum(result.ocr_calls for result in results)

    lines = [
        "# App Row OCR Inspection",
        "",
        "## Summary",
        "",
        f"- Available row images: {total_rows}",
        f"- Inspected row images: {len(results)}",
        f"- Fully parsed rows: {len(results) - review_count}",
        f"- Rows requiring review: {review_count}",
        f"- Review bundles: {review_count} in `review_required/<row_image_stem>/`",
        f"- OCR calls: {total_ocr_calls}",
        f"- Amount fallback rows: {fallback_count}",
        f"- Date-only occurred-at rows: {date_only_count}",
        f"- Split date/time OCR rows: {split_ocr_count}",
        f"- Occurred-at order violations: {order_violation_count}",
        f"- ONNX Runtime CPU threads: {cpu_threads}",
        f"- Merchant OCR minimum-score mean: {mean(merchant_scores) if merchant_scores else 0.0:.4f}",
        f"- Amount OCR minimum-score mean: {mean(amount_scores) if amount_scores else 0.0:.4f}",
        f"- Occurred-at OCR minimum-score mean: {mean(occurred_at_scores) if occurred_at_scores else 0.0:.4f}",
        "",
        "The filename month is not used to construct or validate `occurred_at`. Date-only rows are preserved without inventing a time. Image order is used only to flag suspicious OCR results for review.",
        "",
        "## Results",
        "",
        "| Row image | Merchant | Amount | Occurred at | Scores (merchant / amount / time) | Issues | Notes |",
        "| --- | --- | ---: | --- | --- | --- | --- |",
    ]

    for result in results:
        scores = (
            f"{result.merchant_score:.4f} / {result.amount_score:.4f} / "
            f"{result.occurred_at_score:.4f}"
        )
        lines.append(
            f"| {markdown_cell(result.row_image)} | {markdown_cell(result.merchant_text)} | "
            f"{markdown_cell(result.amount or result.amount_text)} | "
            f"{markdown_cell(result.occurred_at or result.occurred_on or result.occurred_at_text)} | "
            f"{scores} | {markdown_cell(', '.join(result.issues) or 'none')} | "
            f"{markdown_cell(', '.join(result.notes) or 'none')} |"
        )

    review_results = [result for result in results if result.issues]
    if review_results:
        lines.extend(["", "## Review Details", ""])
        for result in review_results:
            lines.extend(
                [
                    f"### {result.row_image}",
                    "",
                    f"- Top-line OCR: `{result.top_line_text}`",
                    f"- Occurred-at initial OCR: `{result.occurred_at_initial_text}`",
                    f"- Occurred-at selected OCR: `{result.occurred_at_text}`",
                    f"- Occurred-at precision: `{result.occurred_at_precision or ''}`",
                    f"- Issues: {', '.join(result.issues)}",
                    f"- Review bundle: `review_required/{Path(result.row_image).stem}/`",
                    "",
                ]
            )

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    if not args.rows_dir.is_dir():
        raise NotADirectoryError(args.rows_dir)

    all_images = find_row_images(args.rows_dir)
    if args.all:
        selected_images = all_images
    elif args.only:
        selected_images = select_named_with_neighbors(all_images, args.only)
    else:
        selected_images = select_evenly(all_images, args.sample_size)
    reset_output_dir(args.rows_dir, args.output_dir)

    logger.info("Rows directory: {}", args.rows_dir)
    logger.info("Available row images: {}", len(all_images))
    logger.info("Selected row images: {}", len(selected_images))
    logger.info("CPU threads: {}", args.cpu_threads)
    logger.info("Output directory reset: {}", args.output_dir)

    engine = create_ocr_engine(args.cpu_threads)
    results: list[RowOCRResult] = []
    for index, row_path in enumerate(selected_images, 1):
        logger.info("OCR row {}/{}: {}", index, len(selected_images), row_path.name)
        result = inspect_row(engine, row_path, args.output_dir)
        results.append(result)
        if result.issues:
            logger.warning("{} issues: {}", row_path.name, ", ".join(result.issues))

    add_order_validation_issues(results)
    write_review_bundles(results, selected_images, all_images, args.output_dir)

    jsonl_path = args.output_dir / "ocr_results.jsonl"
    review_path = args.output_dir / "review.md"
    write_jsonl(results, jsonl_path)
    write_review(results, len(all_images), args.cpu_threads, review_path)

    review_count = sum(bool(result.issues) for result in results)
    logger.info("Inspected row images: {}", len(results))
    logger.info("Fully parsed rows: {}", len(results) - review_count)
    logger.info("Rows requiring review: {}", review_count)
    logger.info("Review: {}", review_path)
    logger.info("Machine-readable results: {}", jsonl_path)


if __name__ == "__main__":
    main()
