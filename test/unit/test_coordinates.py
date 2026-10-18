"""Unit tests for coordinate normalization mathematics."""

import pytest

from internal.vision.coordinates import NormalizedBoundingBox, normalize_bbox


def test_normalize_bbox_top_left_origin() -> None:
    """Test standard computer vision normalization with top-left origin."""
    # 8.5 x 11 inches at 72 DPI: 612 x 792 points
    page_width, page_height = 612.0, 792.0
    box = normalize_bbox(
        x1=61.2,
        y1=79.2,
        x2=306.0,
        y2=396.0,
        page_width=page_width,
        page_height=page_height,
        origin="top-left",
        page_number=1,
    )

    assert isinstance(box, NormalizedBoundingBox)
    assert pytest.approx(box.x1, rel=1e-4) == 0.1
    assert pytest.approx(box.y1, rel=1e-4) == 0.1
    assert pytest.approx(box.x2, rel=1e-4) == 0.5
    assert pytest.approx(box.y2, rel=1e-4) == 0.5
    assert box.page_number == 1


def test_normalize_bbox_bottom_left_pdf_origin() -> None:
    """Test standard PDF normalization with bottom-left origin (PostScript coordinate inversion)."""
    page_width, page_height = 1000.0, 1000.0
    # In bottom-left PDF space, y=800 is near the top (20% from top), y=1000 is the top (0% from top)
    box = normalize_bbox(
        x1=100.0,
        y1=800.0,
        x2=900.0,
        y2=1000.0,
        page_width=page_width,
        page_height=page_height,
        origin="bottom-left",
        page_number=2,
    )

    assert pytest.approx(box.x1, rel=1e-4) == 0.1
    assert pytest.approx(box.x2, rel=1e-4) == 0.9
    # In CV space, y1 should be 0.0 (top edge) and y2 should be 0.2
    assert pytest.approx(box.y1, rel=1e-4) == 0.0
    assert pytest.approx(box.y2, rel=1e-4) == 0.2
    assert box.page_number == 2


def test_normalize_bbox_clamping_and_reordering() -> None:
    """Test that out-of-bound coordinates are clamped and inverted coordinates reordered."""
    box = normalize_bbox(
        x1=1200.0,
        y1=600.0,
        x2=-50.0,
        y2=-100.0,
        page_width=1000.0,
        page_height=500.0,
        origin="top-left",
    )

    assert box.x1 == 0.0
    assert box.y1 == 0.0
    assert box.x2 == 1.0
    assert box.y2 == 1.0
    assert box.x1 <= box.x2
    assert box.y1 <= box.y2


def test_normalize_bbox_invalid_page_dimensions() -> None:
    """Test that non-positive dimensions raise ValueError."""
    with pytest.raises(ValueError, match="strictly positive"):
        normalize_bbox(0, 0, 10, 10, page_width=0.0, page_height=100.0)

    with pytest.raises(ValueError, match="strictly positive"):
        normalize_bbox(0, 0, 10, 10, page_width=100.0, page_height=-5.0)


def test_normalize_bbox_invalid_origin() -> None:
    """Test that unsupported origin parameter raises ValueError."""
    with pytest.raises(ValueError, match="Unsupported coordinate origin"):
        normalize_bbox(0, 0, 10, 10, page_width=100.0, page_height=100.0, origin="center")


def test_normalized_bbox_conversions() -> None:
    """Test tuple and dictionary serializations for Protobuf compatibility."""
    box = normalize_bbox(10, 20, 30, 40, page_width=100.0, page_height=100.0, page_number=5)

    assert box.to_tuple() == (0.1, 0.2, 0.3, 0.4)
    data = box.to_dict()
    assert data["x1"] == 0.1
    assert data["y1"] == 0.2
    assert data["x2"] == 0.3
    assert data["y2"] == 0.4
    assert data["page_number"] == 5
