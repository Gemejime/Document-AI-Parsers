"""Unit tests for multi-pass FallbackOCREngine."""

import uuid

from internal.vision.coordinates import normalize_bbox
from internal.vision.layout.docling_engine import ParsedASTNode
from internal.vision.ocr.fallback_ocr import FallbackOCREngine


def _create_sample_node(text: str, confidence: float) -> ParsedASTNode:
    return ParsedASTNode(
        node_id=str(uuid.uuid4()),
        node_type="paragraph",
        text_content=text,
        bounding_box=normalize_bbox(10, 10, 100, 50, 612, 792),
        confidence=confidence,
    )


def test_fallback_ocr_trigger_condition() -> None:
    """Verify fallback OCR triggers when confidence is below threshold or text is empty."""
    ocr_engine = FallbackOCREngine(confidence_threshold=0.80, use_native=False)

    high_conf_node = _create_sample_node("Clear text here", confidence=0.95)
    low_conf_node = _create_sample_node("Blurry scan text", confidence=0.65)
    empty_node = _create_sample_node("   ", confidence=0.90)

    assert ocr_engine.should_trigger_fallback(high_conf_node) is False
    assert ocr_engine.should_trigger_fallback(low_conf_node) is True
    assert ocr_engine.should_trigger_fallback(empty_node) is True


def test_fallback_ocr_enhancement() -> None:
    """Verify that fallback OCR pass updates confidence score and recovers text."""
    ocr_engine = FallbackOCREngine(confidence_threshold=0.80, use_native=False)

    low_conf_node = _create_sample_node("Faint receipt text", confidence=0.55)
    enhanced = ocr_engine.enhance_node(low_conf_node)

    assert enhanced.node_id == low_conf_node.node_id
    assert enhanced.confidence >= 0.80
    assert enhanced.confidence > low_conf_node.confidence
    assert enhanced.bounding_box == low_conf_node.bounding_box


def test_fallback_ocr_empty_text_recovery() -> None:
    """Verify empty text in visual region is recovered with OCR fallback."""
    ocr_engine = FallbackOCREngine(confidence_threshold=0.80, use_native=False)

    empty_node = _create_sample_node("", confidence=0.70)
    enhanced = ocr_engine.enhance_node(empty_node)

    assert len(enhanced.text_content.strip()) > 0
    assert enhanced.confidence >= 0.88
