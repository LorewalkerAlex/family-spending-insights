from __future__ import annotations

import argparse
import shutil
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, median, pstdev

import numpy as np
from loguru import logger
from PIL import Image, ImageDraw

SCAN_LEFT_RATIO = 0.03
SCAN_RIGHT_RATIO = 0.995
NONWHITE_THRESHOLD = 252
DARK_THRESHOLD = 230
MIN_SEPARATOR_NONWHITE_RATIO = 0.75
MAX_SEPARATOR_DARK_RATIO = 0.02
MIN_SEPARATOR_MEDIAN = 235
MAX_SEPARATOR_MEDIAN = 253
MAX_SEPARATOR_STD = 8.0
MAX_SEPARATOR_GAP = 1
BASELINE_MIN_HEIGHT_RATIO = 0.75
BASELINE_MAX_HEIGHT_RATIO = 1.25
MAX_INTERNAL_DEVIATION_RATIO = 0.10
MIN_EDGE_ROW_HEIGHT_RATIO = 0.50
SUPPORTED_SUFFIXES = {".jpg", ".jpeg", ".png"}


@dataclass(frozen=True)
class SeparatorBand:
    top: int
    bottom: int
    center: int


@dataclass(frozen=True)
class RowCrop:
    top: int
    bottom: int
    source: str

    @property
    def height(self) -> int:
        return self.bottom - self.top


@dataclass(frozen=True)
class HeightAnalysis:
    heights: list[int]
    baseline_heights: list[int]
    typical_height: float
    raw_mean: float
    raw_median: float
    raw_std: float


@dataclass(frozen=True)
class InternalSegment:
    index: int
    top: int
    bottom: int
    height: int
    deviation_ratio: float
    is_anomaly: bool


@dataclass(frozen=True)
class EdgeSegment:
    position: str
    available_top: int
    available_bottom: int
    available_height: int
    crop_top: int | None
    crop_bottom: int | None
    crop_height: int
    available_ratio: float
    is_kept: bool


@dataclass(frozen=True)
class SavedRow:
    filename: str
    source_image: str
    source_index: int
    top: int
    bottom: int
    height: int
    source: str


@dataclass(frozen=True)
class SavedRejectedSegment:
    filename: str
    source_image: str
    top: int
    bottom: int
    height: int
    source: str


@dataclass(frozen=True)
class InspectionResult:
    image_path: Path
    width: int
    height: int
    separator_count: int
    rows: list[SavedRow]
    rejected: list[SavedRejectedSegment]
    anomaly_count: int
    typical_height: float
    top_edge: EdgeSegment
    bottom_edge: EdgeSegment

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def rejected_count(self) -> int:
        return len(self.rejected)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect and split transaction rows from every App screenshot in a directory."
    )
    parser.add_argument("image_dir", type=Path, help="Directory containing App screenshots")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("tmp/app_row_inspection"),
        help="Output directory; deleted and rebuilt on every run",
    )
    return parser.parse_args()


def find_images(image_dir: Path) -> list[Path]:
    images = sorted(
        path
        for path in image_dir.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    )
    if not images:
        supported = ", ".join(sorted(SUPPORTED_SUFFIXES))
        raise ValueError(f"No supported images found in {image_dir}; expected one of: {supported}")

    stems = [path.stem for path in images]
    duplicates = sorted({stem for stem in stems if stems.count(stem) > 1})
    if duplicates:
        raise ValueError(f"Duplicate image stems would overwrite outputs: {duplicates}")
    return images


def reset_output_dir(image_dir: Path, output_dir: Path) -> None:
    image_dir_resolved = image_dir.resolve()
    output_dir_resolved = output_dir.resolve()
    current_dir_resolved = Path.cwd().resolve()

    if output_dir_resolved == image_dir_resolved or output_dir_resolved in image_dir_resolved.parents:
        raise ValueError(
            f"Refusing to delete output directory because it contains the image directory: {output_dir}"
        )
    if output_dir_resolved == current_dir_resolved or output_dir_resolved == Path(output_dir_resolved.anchor):
        raise ValueError(f"Refusing to delete unsafe output directory: {output_dir}")

    if output_dir.exists():
        shutil.rmtree(output_dir)

    for name in ("rows", "rejected_segments", "previews", "diagnostics"):
        (output_dir / name).mkdir(parents=True, exist_ok=True)


def find_separator_bands(image: Image.Image) -> list[SeparatorBand]:
    gray = np.asarray(image.convert("L"), dtype=np.uint8)
    left = max(0, round(image.width * SCAN_LEFT_RATIO))
    right = min(image.width, round(image.width * SCAN_RIGHT_RATIO))
    strip = gray[:, left:right]

    nonwhite_ratio = np.mean(strip <= NONWHITE_THRESHOLD, axis=1)
    dark_ratio = np.mean(strip < DARK_THRESHOLD, axis=1)
    row_median = np.median(strip, axis=1)
    row_std = np.std(strip, axis=1)
    active = (
        (nonwhite_ratio >= MIN_SEPARATOR_NONWHITE_RATIO)
        & (dark_ratio <= MAX_SEPARATOR_DARK_RATIO)
        & (row_median >= MIN_SEPARATOR_MEDIAN)
        & (row_median <= MAX_SEPARATOR_MEDIAN)
        & (row_std <= MAX_SEPARATOR_STD)
    )

    bands: list[SeparatorBand] = []
    start: int | None = None
    last_active: int | None = None

    for y, is_active in enumerate(active):
        if is_active:
            if start is None:
                start = y
            last_active = y
            continue
        if start is None or last_active is None or y - last_active <= MAX_SEPARATOR_GAP:
            continue
        bands.append(SeparatorBand(start, last_active, round((start + last_active) / 2)))
        start = None
        last_active = None

    if start is not None and last_active is not None:
        bands.append(SeparatorBand(start, last_active, round((start + last_active) / 2)))

    return bands


def analyze_internal_heights(bands: list[SeparatorBand]) -> HeightAnalysis:
    if len(bands) < 2:
        raise ValueError("Could not find enough horizontal separators to determine transaction-row height")

    centers = [band.center for band in bands]
    heights = [bottom - top for top, bottom in zip(centers, centers[1:], strict=False)]
    raw_median = float(median(heights))
    baseline_heights = [
        height
        for height in heights
        if BASELINE_MIN_HEIGHT_RATIO * raw_median <= height <= BASELINE_MAX_HEIGHT_RATIO * raw_median
    ]
    if not baseline_heights:
        raise ValueError("Could not find a stable group of separator distances")

    return HeightAnalysis(
        heights=heights,
        baseline_heights=baseline_heights,
        typical_height=float(mean(baseline_heights)),
        raw_mean=float(mean(heights)),
        raw_median=raw_median,
        raw_std=float(pstdev(heights)) if len(heights) > 1 else 0.0,
    )


def build_row_crops(
    bands: list[SeparatorBand], image_height: int, analysis: HeightAnalysis
) -> tuple[list[RowCrop], list[RowCrop], list[InternalSegment], list[EdgeSegment]]:
    centers = [band.center for band in bands]
    minimum_edge_height = analysis.typical_height * MIN_EDGE_ROW_HEIGHT_RATIO
    typical_crop_height = max(1, round(analysis.typical_height))
    rows: list[RowCrop] = []
    rejected: list[RowCrop] = []
    internal_segments: list[InternalSegment] = []
    edge_segments: list[EdgeSegment] = []

    top_available_height = centers[0]
    top_kept = top_available_height >= minimum_edge_height
    top_crop_height = min(top_available_height, typical_crop_height) if top_kept else 0
    top_crop_start = centers[0] - top_crop_height if top_kept else None
    edge_segments.append(
        EdgeSegment(
            position="top",
            available_top=0,
            available_bottom=centers[0],
            available_height=top_available_height,
            crop_top=top_crop_start,
            crop_bottom=centers[0] if top_kept else None,
            crop_height=top_crop_height,
            available_ratio=top_available_height / analysis.typical_height,
            is_kept=top_kept,
        )
    )
    if top_available_height > 0:
        if top_kept:
            rows.append(RowCrop(top_crop_start, centers[0], "top-edge"))
        else:
            rejected.append(RowCrop(0, centers[0], "top-edge-separator-fragment"))

    for index, (top, bottom) in enumerate(zip(centers, centers[1:], strict=False), 1):
        height = bottom - top
        deviation_ratio = abs(height - analysis.typical_height) / analysis.typical_height
        is_anomaly = deviation_ratio > MAX_INTERNAL_DEVIATION_RATIO
        internal_segments.append(InternalSegment(index, top, bottom, height, deviation_ratio, is_anomaly))
        crop = RowCrop(top, bottom, "between-separators")
        if is_anomaly:
            rejected.append(crop)
        else:
            rows.append(crop)

    bottom_available_height = image_height - centers[-1]
    bottom_kept = bottom_available_height >= minimum_edge_height
    bottom_crop_height = min(bottom_available_height, typical_crop_height) if bottom_kept else 0
    bottom_crop_end = centers[-1] + bottom_crop_height if bottom_kept else None
    edge_segments.append(
        EdgeSegment(
            position="bottom",
            available_top=centers[-1],
            available_bottom=image_height,
            available_height=bottom_available_height,
            crop_top=centers[-1] if bottom_kept else None,
            crop_bottom=bottom_crop_end,
            crop_height=bottom_crop_height,
            available_ratio=bottom_available_height / analysis.typical_height,
            is_kept=bottom_kept,
        )
    )
    if bottom_available_height > 0:
        if bottom_kept:
            rows.append(RowCrop(centers[-1], bottom_crop_end, "bottom-edge"))
        else:
            rejected.append(RowCrop(centers[-1], image_height, "bottom-edge-separator-fragment"))

    return rows, rejected, internal_segments, edge_segments


def save_preview(
    image: Image.Image,
    bands: list[SeparatorBand],
    rows: list[RowCrop],
    output_path: Path,
) -> None:
    preview = image.copy()
    draw = ImageDraw.Draw(preview)
    line_width = max(2, image.width // 640)

    for band in bands:
        draw.line((0, band.center, image.width - 1, band.center), fill=(255, 0, 0), width=line_width)
    for index, crop in enumerate(rows, 1):
        draw.text((8, crop.top + 8), str(index), fill=(255, 0, 0))

    preview.save(output_path, quality=92)


def save_rows(
    image: Image.Image,
    image_path: Path,
    rows: list[RowCrop],
    rows_dir: Path,
) -> list[SavedRow]:
    saved: list[SavedRow] = []
    for index, crop in enumerate(rows, 1):
        filename = f"{image_path.stem}__{index:03d}.jpg"
        image.crop((0, crop.top, image.width, crop.bottom)).save(rows_dir / filename, quality=95)
        saved.append(
            SavedRow(
                filename=filename,
                source_image=image_path.name,
                source_index=index,
                top=crop.top,
                bottom=crop.bottom,
                height=crop.height,
                source=crop.source,
            )
        )
    return saved


def save_rejected_segments(
    image: Image.Image,
    image_path: Path,
    rejected: list[RowCrop],
    rejected_dir: Path,
) -> list[SavedRejectedSegment]:
    saved: list[SavedRejectedSegment] = []
    for index, crop in enumerate(rejected, 1):
        filename = (
            f"{image_path.stem}__{index:03d}__{crop.source}__{crop.top}-{crop.bottom}.jpg"
        )
        image.crop((0, crop.top, image.width, crop.bottom)).save(
            rejected_dir / filename, quality=95
        )
        saved.append(
            SavedRejectedSegment(
                filename=filename,
                source_image=image_path.name,
                top=crop.top,
                bottom=crop.bottom,
                height=crop.height,
                source=crop.source,
            )
        )
    return saved


def write_image_diagnostics(
    image_path: Path,
    analysis: HeightAnalysis,
    internal_segments: list[InternalSegment],
    edge_segments: list[EdgeSegment],
    saved_rows: list[SavedRow],
    saved_rejected: list[SavedRejectedSegment],
    output_path: Path,
) -> None:
    anomalies = [segment for segment in internal_segments if segment.is_anomaly]
    excluded_from_baseline = [height for height in analysis.heights if height not in analysis.baseline_heights]
    lines = [
        f"# {image_path.name}",
        "",
        f"- Saved transaction rows: {len(saved_rows)}",
        f"- Rejected segments: {len(saved_rejected)}",
        f"- Internal segments: {len(analysis.heights)}",
        f"- Internal mean: {analysis.raw_mean:.2f}px",
        f"- Internal median: {analysis.raw_median:.2f}px",
        f"- Internal standard deviation: {analysis.raw_std:.2f}px",
        f"- Internal minimum: {min(analysis.heights)}px",
        f"- Internal maximum: {max(analysis.heights)}px",
        f"- Baseline segments: {len(analysis.baseline_heights)}",
        f"- Typical row height: {analysis.typical_height:.2f}px",
        f"- Internal anomaly threshold: ±{MAX_INTERNAL_DEVIATION_RATIO:.0%}",
        f"- Edge minimum height: {MIN_EDGE_ROW_HEIGHT_RATIO:.0%} of typical height",
        "",
        "## Edge Segments",
        "",
    ]

    for segment in edge_segments:
        decision = "kept" if segment.is_kept else "rejected as separator fragment"
        crop_range = f"{segment.crop_top}-{segment.crop_bottom}" if segment.is_kept else "none"
        lines.append(
            f"- {segment.position}: available={segment.available_top}-{segment.available_bottom} "
            f"({segment.available_height}px, {segment.available_ratio:.1%}), "
            f"crop={crop_range} ({segment.crop_height}px), decision={decision}"
        )

    lines.extend(
        [
            "",
            "## Internal Height Distribution",
            "",
            f"`{analysis.heights}`",
            "",
            "## Heights Excluded From Baseline",
            "",
            f"`{excluded_from_baseline}`",
            "",
            "## Anomalous Internal Segments",
            "",
        ]
    )

    if anomalies:
        for segment in anomalies:
            lines.append(
                f"- Segment {segment.index}: y={segment.top}-{segment.bottom}, "
                f"height={segment.height}px, deviation={segment.deviation_ratio:.1%}"
            )
    else:
        lines.append("- None")

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def inspect_image(image_path: Path, output_dir: Path) -> InspectionResult:
    with Image.open(image_path) as source:
        image = source.convert("RGB")

    bands = find_separator_bands(image)
    analysis = analyze_internal_heights(bands)
    rows, rejected, internal_segments, edge_segments = build_row_crops(
        bands, image.height, analysis
    )

    saved_rows = save_rows(image, image_path, rows, output_dir / "rows")
    saved_rejected = save_rejected_segments(
        image, image_path, rejected, output_dir / "rejected_segments"
    )
    save_preview(
        image,
        bands,
        rows,
        output_dir / "previews" / f"{image_path.stem}.jpg",
    )
    write_image_diagnostics(
        image_path=image_path,
        analysis=analysis,
        internal_segments=internal_segments,
        edge_segments=edge_segments,
        saved_rows=saved_rows,
        saved_rejected=saved_rejected,
        output_path=output_dir / "diagnostics" / f"{image_path.stem}.md",
    )

    anomalies = [segment for segment in internal_segments if segment.is_anomaly]
    top_edge, bottom_edge = edge_segments
    logger.info(
        "{}: size={}x{}, separators={}, rows={}, typical_height={:.2f}px, anomalies={}, rejected={}",
        image_path.name,
        image.width,
        image.height,
        len(bands),
        len(saved_rows),
        analysis.typical_height,
        len(anomalies),
        len(saved_rejected),
    )
    logger.info(
        "{} top edge: available={}px, crop={}px, decision={}",
        image_path.name,
        top_edge.available_height,
        top_edge.crop_height,
        "keep" if top_edge.is_kept else "reject separator fragment",
    )
    logger.info(
        "{} bottom edge: available={}px, crop={}px, decision={}",
        image_path.name,
        bottom_edge.available_height,
        bottom_edge.crop_height,
        "keep" if bottom_edge.is_kept else "reject separator fragment",
    )
    for segment in anomalies:
        logger.warning(
            "{} abnormal segment {}: y={}-{}, height={}px, deviation={:.1%}",
            image_path.name,
            segment.index,
            segment.top,
            segment.bottom,
            segment.height,
            segment.deviation_ratio,
        )

    return InspectionResult(
        image_path=image_path,
        width=image.width,
        height=image.height,
        separator_count=len(bands),
        rows=saved_rows,
        rejected=saved_rejected,
        anomaly_count=len(anomalies),
        typical_height=analysis.typical_height,
        top_edge=top_edge,
        bottom_edge=bottom_edge,
    )


def format_edge_summary(segment: EdgeSegment) -> str:
    decision = "keep" if segment.is_kept else "reject"
    return f"{segment.available_height}px → {segment.crop_height}px ({decision})"


def write_summary(results: list[InspectionResult], output_path: Path) -> None:
    all_rows = [row for result in results for row in result.rows]
    all_heights = [row.height for row in all_rows]
    total_anomalies = sum(result.anomaly_count for result in results)
    total_rejected = sum(result.rejected_count for result in results)

    lines = [
        "# App Row Inspection Summary",
        "",
        "## All Saved Transaction Rows",
        "",
        f"- Input images: {len(results)}",
        f"- Saved transaction row images: {len(all_rows)}",
        f"- Row height mean: {mean(all_heights):.2f}px",
        f"- Row height median: {median(all_heights):.2f}px",
        f"- Row height standard deviation: {pstdev(all_heights) if len(all_heights) > 1 else 0.0:.2f}px",
        f"- Row height minimum: {min(all_heights)}px",
        f"- Row height maximum: {max(all_heights)}px",
        f"- Rejected segments: {total_rejected}",
        f"- Abnormal internal segments: {total_anomalies}",
        f"- Edge minimum height ratio: {MIN_EDGE_ROW_HEIGHT_RATIO:.0%}",
        "",
        "All successful row crops are stored together in `rows/`.",
        "",
        "## Per-image Diagnostics",
        "",
        "| Image | Size | Separators | Saved rows | Typical height | Internal anomalies | Rejected | Top edge | Bottom edge |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |",
    ]

    for result in results:
        lines.append(
            f"| {result.image_path.name} | {result.width}x{result.height} | "
            f"{result.separator_count} | {result.row_count} | {result.typical_height:.2f}px | "
            f"{result.anomaly_count} | {result.rejected_count} | "
            f"{format_edge_summary(result.top_edge)} | {format_edge_summary(result.bottom_edge)} |"
        )

    review_results = [
        result for result in results if result.anomaly_count > 0 or result.rejected_count > 0
    ]
    lines.extend(["", "## Review Required", ""])
    if review_results:
        for result in review_results:
            lines.append(
                f"- {result.image_path.name}: internal anomalies={result.anomaly_count}, "
                f"rejected={result.rejected_count}"
            )
    else:
        lines.append("- None")

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    if not args.image_dir.is_dir():
        raise NotADirectoryError(args.image_dir)

    images = find_images(args.image_dir)
    reset_output_dir(args.image_dir, args.output_dir)
    logger.info("Input directory: {}", args.image_dir)
    logger.info("Images found: {}", len(images))
    logger.info("Output directory reset: {}", args.output_dir)

    results: list[InspectionResult] = []
    for index, image_path in enumerate(images, 1):
        logger.info("Processing image {}/{}: {}", index, len(images), image_path.name)
        results.append(inspect_image(image_path, args.output_dir))

    summary_path = args.output_dir / "summary.md"
    write_summary(results, summary_path)

    total_rows = sum(result.row_count for result in results)
    total_anomalies = sum(result.anomaly_count for result in results)
    total_rejected = sum(result.rejected_count for result in results)
    logger.info("Processed images: {}", len(results))
    logger.info("Saved transaction row images: {}", total_rows)
    logger.info("Abnormal internal segments: {}", total_anomalies)
    logger.info("Rejected segments: {}", total_rejected)
    logger.info("Rows directory: {}", args.output_dir / "rows")
    logger.info("Summary: {}", summary_path)


if __name__ == "__main__":
    main()