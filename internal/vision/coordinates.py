"""Coordinate normalization math strictly mapping coordinates to [0.0, 1.0]."""

from dataclasses import dataclass


@dataclass(frozen=True)
class NormalizedBoundingBox:
    """Immutable normalized bounding box with top-left origin (0.0, 0.0)."""

    x1: float
    y1: float
    x2: float
    y2: float
    page_number: int = 1

    def to_tuple(self) -> tuple[float, float, float, float]:
        """Return (x1, y1, x2, y2) tuple."""
        return (self.x1, self.y1, self.x2, self.y2)

    def to_dict(self) -> dict[str, float | int]:
        """Convert to dictionary matching Protobuf common.v1.BoundingBox."""
        return {
            "x1": self.x1,
            "y1": self.y1,
            "x2": self.x2,
            "y2": self.y2,
            "page_number": self.page_number,
        }


def _clamp(val: float, min_val: float = 0.0, max_val: float = 1.0) -> float:
    """Clamp float to [min_val, max_val]."""
    return max(min_val, min(max_val, val))


def normalize_bbox(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    page_width: float,
    page_height: float,
    origin: str = "top-left",
    page_number: int = 1,
) -> NormalizedBoundingBox:
    """Normalize bounding box coordinates to [0.0, 1.0] anchored at top-left origin.

    Args:
        x1: Left or horizontal coordinate.
        y1: Top or bottom vertical coordinate depending on origin.
        x2: Right or horizontal coordinate.
        y2: Top or bottom vertical coordinate depending on origin.
        page_width: Width of page canvas in source units (e.g. 72 DPI points or pixels).
        page_height: Height of page canvas in source units.
        origin: Coordinate origin convention ('top-left' for images/CV, 'bottom-left' for raw PDF).
        page_number: 1-indexed page number.

    Returns:
        NormalizedBoundingBox with (x1, y1) as top-left and (x2, y2) as bottom-right in [0.0, 1.0].

    Raises:
        ValueError: If page dimensions are non-positive or origin is unsupported.
    """
    if page_width <= 0.0 or page_height <= 0.0:
        raise ValueError(
            f"Page dimensions must be strictly positive: got width={page_width}, height={page_height}"
        )

    clean_origin = origin.lower().strip()
    if clean_origin not in ("top-left", "bottom-left"):
        raise ValueError(
            f"Unsupported coordinate origin '{origin}'. Supported values: 'top-left', 'bottom-left'"
        )

    # Reorder coordinates to ensure min <= max in source space
    min_x = min(x1, x2)
    max_x = max(x1, x2)
    min_y = min(y1, y2)
    max_y = max(y1, y2)

    norm_x1 = _clamp(min_x / page_width)
    norm_x2 = _clamp(max_x / page_width)

    if clean_origin == "top-left":
        # (0,0) is top-left, y increases downward
        norm_y1 = _clamp(min_y / page_height)
        norm_y2 = _clamp(max_y / page_height)
    else:
        # (0,0) is bottom-left, y increases upward (standard PDF specification)
        # Top in CV becomes 1.0 - (max_y / height)
        # Bottom in CV becomes 1.0 - (min_y / height)
        norm_y1 = _clamp(1.0 - (max_y / page_height))
        norm_y2 = _clamp(1.0 - (min_y / page_height))

    # Guard against float rounding inversions
    final_x1 = min(norm_x1, norm_x2)
    final_x2 = max(norm_x1, norm_x2)
    final_y1 = min(norm_y1, norm_y2)
    final_y2 = max(norm_y1, norm_y2)

    return NormalizedBoundingBox(
        x1=round(final_x1, 6),
        y1=round(final_y1, 6),
        x2=round(final_x2, 6),
        y2=round(final_y2, 6),
        page_number=page_number,
    )
