"""Vision package for document layout, OCR, table extraction, and coordinate math."""

from internal.vision.coordinates import NormalizedBoundingBox, normalize_bbox


__all__ = ["NormalizedBoundingBox", "normalize_bbox"]
