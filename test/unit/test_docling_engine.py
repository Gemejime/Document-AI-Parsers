import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

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


def test_docling_engine_native_missing_file_raises_filenotfound() -> None:
    """Verify that in native mode, missing file raises FileNotFoundError without falling through."""
    engine = DoclingEngine(use_native=True)
    with pytest.raises(FileNotFoundError, match="PDF slice file not found"):
        list(engine.parse_slice_pages(pdf_path="non_existent_file.pdf"))


def test_docling_engine_native_converter_unavailable_raises_runtime_error(tmp_path: Path) -> None:
    """Verify that in native mode, unavailable converter raises RuntimeError without fall-through."""
    test_pdf = tmp_path / "valid_slice.pdf"
    test_pdf.write_bytes(b"%PDF-1.4 mock content")

    engine = DoclingEngine(use_native=False)
    engine.use_native = True
    engine._converter = None

    with pytest.raises(
        RuntimeError, match="Native Docling converter is not initialized or unavailable"
    ):
        list(engine.parse_slice_pages(pdf_path=str(test_pdf)))


def test_docling_engine_native_delegation_no_fallthrough(tmp_path: Path) -> None:
    """Verify native mode delegates to _parse_with_docling_native and does not generate synthetic pages."""
    test_pdf = tmp_path / "valid_slice.pdf"
    test_pdf.write_bytes(b"%PDF-1.4 mock content")

    engine = DoclingEngine(use_native=False)
    engine.use_native = True

    mock_converter = MagicMock()
    mock_doc = MagicMock()
    mock_doc.pages = [MagicMock()]
    mock_doc.iterate_items.return_value = []
    mock_conv_res = MagicMock()
    mock_conv_res.document = mock_doc
    mock_converter.convert.return_value = mock_conv_res

    engine._converter = mock_converter

    pages = list(engine.parse_slice_pages(pdf_path=str(test_pdf), total_pages=1))
    assert len(pages) == 1
    assert mock_converter.convert.called
    # In native mode with empty items, nodes should be empty (synthetic pages would have heading, paragraph, table)
    assert len(pages[0].nodes) == 0
