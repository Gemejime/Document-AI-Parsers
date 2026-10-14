"""Table structure extraction, Markdown summarization, and PyArrow serialization."""

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

import pyarrow as pa

from internal.config.settings import structlog
from internal.vision.coordinates import NormalizedBoundingBox, normalize_bbox


logger = structlog.get_logger(__name__)


@dataclass
class TableData:
    """Structured representation of an extracted document table."""

    headers: list[str]
    rows: list[list[Any]]
    bbox: NormalizedBoundingBox
    page_number: int
    caption: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_markdown(self) -> str:
        """Generate clean, semantically dense GitHub-Flavored Markdown (GFM)."""
        if not self.headers and not self.rows:
            return ""

        effective_headers = (
            self.headers
            if self.headers
            else [f"col_{i}" for i in range(len(self.rows[0]) if self.rows else 0)]
        )
        num_cols = len(effective_headers)

        def clean_cell(cell: Any) -> str:
            if cell is None:
                return ""
            val = str(cell).replace("\n", " ").replace("\r", " ").strip()
            return val.replace("|", "\\|")

        header_line = "| " + " | ".join(clean_cell(h) for h in effective_headers) + " |"
        sep_line = "| " + " | ".join(["---"] * num_cols) + " |"

        row_lines: list[str] = []
        for row in self.rows:
            # Normalize row length to match headers
            padded_row = list(row) + [""] * (num_cols - len(row))
            row_str = "| " + " | ".join(clean_cell(c) for c in padded_row[:num_cols]) + " |"
            row_lines.append(row_str)

        lines = [header_line, sep_line, *row_lines]
        if self.caption:
            lines.insert(0, f"**Table: {clean_cell(self.caption)}**\n")
        return "\n".join(lines)

    def compute_table_hash(self) -> str:
        """Compute deterministic SHA-256 table hash using canonical JSON serialization."""
        canonical_repr = json.dumps(
            {
                "headers": [str(h) for h in self.headers],
                "rows": [[str(c) if c is not None else "" for c in row] for row in self.rows],
            },
            sort_keys=True,
        )
        return hashlib.sha256(canonical_repr.encode("utf-8")).hexdigest()

    def to_pyarrow_table(self) -> pa.Table:
        """Convert tabular matrix into columnar PyArrow Table with strongly-typed columns."""
        effective_headers = [
            str(h).strip() if str(h).strip() else f"col_{i}" for i, h in enumerate(self.headers)
        ]
        num_cols = len(effective_headers)

        # Normalize rows to match column count
        normalized_rows: list[list[Any]] = []
        for r in self.rows:
            norm_r = list(r)
            if len(norm_r) < num_cols:
                norm_r.extend([None] * (num_cols - len(norm_r)))
            normalized_rows.append(norm_r[:num_cols])

        # Infer column types and construct pyarrow arrays
        columns: dict[str, pa.Array] = {}
        for col_idx, col_name in enumerate(effective_headers):
            col_values = [row[col_idx] for row in normalized_rows]
            columns[col_name] = _infer_and_build_array(col_values)

        return pa.Table.from_pydict(columns)


def _infer_and_build_array(values: list[Any]) -> pa.Array:
    """Infer optimal PyArrow data type for a sequence of values."""
    if not values:
        return pa.array([], type=pa.string())

    non_null_values = [v for v in values if v is not None and str(v).strip() != ""]

    if not non_null_values:
        return pa.array(
            [None if v is None or str(v).strip() == "" else str(v) for v in values],
            type=pa.string(),
        )

    # Check for boolean
    is_bool = True
    for v in non_null_values:
        if isinstance(v, bool):
            continue
        if str(v).lower() in ("true", "false", "yes", "no"):
            continue
        is_bool = False
        break

    if is_bool:
        bool_vals = [
            (str(v).lower() in ("true", "yes")) if v is not None and str(v).strip() != "" else None
            for v in values
        ]
        return pa.array(bool_vals, type=pa.bool_())

    # Check for integer
    is_int = True
    for v in non_null_values:
        try:
            int(str(v).replace(",", ""))
        except (ValueError, TypeError):
            is_int = False
            break

    if is_int:
        int_vals = [
            int(str(v).replace(",", "")) if v is not None and str(v).strip() != "" else None
            for v in values
        ]
        return pa.array(int_vals, type=pa.int64())

    # Check for float
    is_float = True
    for v in non_null_values:
        try:
            float(str(v).replace(",", ""))
        except (ValueError, TypeError):
            is_float = False
            break

    if is_float:
        float_vals = [
            float(str(v).replace(",", "")) if v is not None and str(v).strip() != "" else None
            for v in values
        ]
        return pa.array(float_vals, type=pa.float64())

    # Fallback to string
    str_vals = [str(v) if v is not None else None for v in values]
    return pa.array(str_vals, type=pa.string())


class TableExtractor:
    """Boundary detection and structural table extraction engine."""

    def __init__(self, confidence_threshold: float = 0.70) -> None:
        self.confidence_threshold = confidence_threshold

    def extract_from_raw_grid(
        self,
        headers: list[str],
        rows: list[list[Any]],
        page_number: int,
        bbox_coords: tuple[float, float, float, float] = (0.05, 0.2, 0.95, 0.6),
        page_width: float = 612.0,
        page_height: float = 792.0,
        caption: str = "",
    ) -> TableData:
        """Create TableData from raw header and row grid with coordinate normalization."""
        norm_bbox = normalize_bbox(
            x1=bbox_coords[0],
            y1=bbox_coords[1],
            x2=bbox_coords[2],
            y2=bbox_coords[3],
            page_width=page_width,
            page_height=page_height,
            origin="top-left",
            page_number=page_number,
        )
        return TableData(
            headers=headers,
            rows=rows,
            bbox=norm_bbox,
            page_number=page_number,
            caption=caption,
        )
