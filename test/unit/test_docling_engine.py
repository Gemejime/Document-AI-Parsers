"""Unit tests for Docling layout engine adapter, streaming, coordinates, and cancellation."""

import threading

from internal.vision.layout.docling_engine import DoclingEngine, PageLayoutResult


def test_docling_engine_simulated_page_generation() -> None:
    """Verify that DoclingEngine decomposes page into structured AST nodes with normalized coordinates."""
    engine = DoclingEngine(use_native=False)
    results = list(engine.parse_slice_pages(pdf_path="test_slice.pdf", total_pages=3))

    assert len(results) == 3

    for idx, page_res in enumerate(results):
        assert isinstance(page_res, PageLayoutResult)
        assert page_res.page_number == idx + 1
        assert len(page_res.nodes) >= 3

        # Check node types
        types = [n.node_type for n in page_res.nodes]
        assert "heading_h1" in types
        assert "paragraph" in types
        assert "table" in types

        # Check bounding box normalization
        for node in page_res.nodes:
            bbox = node.bounding_box
            assert 0.0 <= bbox.x1 <= 1.0
            assert 0.0 <= bbox.y1 <= 1.0
            assert 0.0 <= bbox.x2 <= 1.0
            assert 0.0 <= bbox.y2 <= 1.0
            assert bbox.x1 <= bbox.x2
            assert bbox.y1 <= bbox.y2
            assert bbox.page_number == page_res.page_number


def test_docling_engine_resumption_skips_pages() -> None:
    """Verify that start_from_page skips earlier pages for mid-slice recovery."""
    engine = DoclingEngine(use_native=False)
    # 5 total pages, resume from page index 3 (page 4)
    results = list(
        engine.parse_slice_pages(pdf_path="test_slice.pdf", start_from_page=3, total_pages=5)
    )

    assert len(results) == 2
    assert results[0].page_number == 4
    assert results[1].page_number == 5


def test_docling_engine_cooperative_cancellation() -> None:
    """Verify that stop_event halts page iteration cooperatively across thread boundary."""
    engine = DoclingEngine(use_native=False)
    stop_event = threading.Event()
    stop_event.set()  # Cancel immediately

    results = list(
        engine.parse_slice_pages(
            pdf_path="test_slice.pdf",
            total_pages=10,
            stop_event=stop_event,
        )
    )

    assert len(results) == 0


def test_docling_engine_progress_callback() -> None:
    """Verify on_page_complete callback is invoked for each processed page."""
    engine = DoclingEngine(use_native=False)
    completed_pages: list[int] = []

    def on_complete(page_res: PageLayoutResult) -> None:
        completed_pages.append(page_res.page_number)

    list(
        engine.parse_slice_pages(
            pdf_path="test_slice.pdf",
            total_pages=4,
            on_page_complete=on_complete,
        )
    )

    assert completed_pages == [1, 2, 3, 4]
