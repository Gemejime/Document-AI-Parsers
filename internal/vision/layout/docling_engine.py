"""Docling layout parser adapter with streaming page iteration and unit coordinates."""

import os
import threading
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from internal.config.settings import structlog
from internal.vision.coordinates import NormalizedBoundingBox, normalize_bbox
from internal.vision.memory import cleanup_vram_and_memory
from internal.vision.table.table_extractor import TableData, TableExtractor


logger = structlog.get_logger(__name__)


@dataclass
class ParsedASTNode:
    """Decomposed structural node within a parsed document layout."""

    node_id: str
    node_type: str  # paragraph, heading_h1..h6, list, table
    text_content: str
    bounding_box: NormalizedBoundingBox
    confidence: float
    table_parquet_s3_uri: str | None = None
    table_data: TableData | None = None


@dataclass
class PageLayoutResult:
    """Decomposed layout result for a single document page."""

    page_number: int
    nodes: list[ParsedASTNode]


class DoclingEngine:
    """Adapter for Docling document layout decomposition with fallback and streaming support."""

    def __init__(self, use_native: bool = True) -> None:
        self.use_native = use_native
        self.table_extractor = TableExtractor()
        self._converter: Any = None

        if self.use_native:
            try:
                from docling.document_converter import DocumentConverter

                self._converter = DocumentConverter()
                logger.info("docling_native_converter_initialized")
            except Exception as exc:
                logger.warning(
                    "docling_native_converter_unavailable_falling_back_to_internal_parser",
                    error=str(exc),
                )
                self._converter = None

    def parse_slice_pages(
        self,
        pdf_path: str,
        start_from_page: int = 0,
        total_pages: int | None = None,
        stop_event: threading.Event | None = None,
        on_page_complete: Callable[[PageLayoutResult], None] | None = None,
        page_width: float = 612.0,
        page_height: float = 792.0,
    ) -> Iterator[PageLayoutResult]:
        """Progressively stream parsed pages across thread boundary with cooperative cancellation.

        Args:
            pdf_path: Path to local PDF slice file.
            start_from_page: 0-indexed starting page to skip already completed pages.
            total_pages: Total number of pages expected in slice.
            stop_event: Threading event for cooperative cancellation.
            on_page_complete: Optional callback invoked after each page is parsed.
            page_width: Source canvas width in points.
            page_height: Source canvas height in points.

        Yields:
            PageLayoutResult containing normalized AST nodes.
        """
        if self.use_native:
            if not os.path.exists(pdf_path):
                raise FileNotFoundError(f"PDF slice file not found: {pdf_path}")
            if self._converter is None:
                raise RuntimeError("Native Docling converter is not initialized or unavailable")

            yield from self._parse_with_docling_native(
                pdf_path=pdf_path,
                start_from_page=start_from_page,
                total_pages=total_pages,
                stop_event=stop_event,
                on_page_complete=on_page_complete,
            )
            return

        # Synthetic page generation (only active when use_native=False)
        if not os.path.exists(pdf_path) and not pdf_path.endswith(".pdf"):
            raise FileNotFoundError(f"PDF slice file not found: {pdf_path}")

        # Resilient internal parser / test mock parser
        effective_page_count = total_pages if total_pages is not None else 1
        for page_idx in range(effective_page_count):
            if stop_event is not None and stop_event.is_set():
                logger.info("docling_parsing_cancelled_by_stop_event", page_idx=page_idx)
                break

            # Resumption skip
            if page_idx < start_from_page:
                continue

            page_num = page_idx + 1
            result = self._generate_simulated_page(
                page_number=page_num,
                page_width=page_width,
                page_height=page_height,
            )

            if on_page_complete is not None:
                on_page_complete(result)

            yield result

            # Proactive VRAM and heap memory reclamation
            cleanup_vram_and_memory()

    def _generate_simulated_page(
        self,
        page_number: int,
        page_width: float,
        page_height: float,
    ) -> PageLayoutResult:
        """Construct synthetic layout result for testing environments and fallback mode."""
        nodes: list[ParsedASTNode] = []

        # 1. Heading H1
        h1_bbox = normalize_bbox(
            50.0,
            50.0,
            550.0,
            80.0,
            page_width=page_width,
            page_height=page_height,
            origin="top-left",
            page_number=page_number,
        )
        nodes.append(
            ParsedASTNode(
                node_id=str(uuid.uuid4()),
                node_type="heading_h1",
                text_content=f"# Document Section Page {page_number}",
                bounding_box=h1_bbox,
                confidence=0.98,
            )
        )

        # 2. Paragraph
        p_bbox = normalize_bbox(
            50.0,
            90.0,
            550.0,
            160.0,
            page_width=page_width,
            page_height=page_height,
            origin="top-left",
            page_number=page_number,
        )
        nodes.append(
            ParsedASTNode(
                node_id=str(uuid.uuid4()),
                node_type="paragraph",
                text_content=f"This is an extracted layout paragraph on page {page_number}.",
                bounding_box=p_bbox,
                confidence=0.95,
            )
        )

        # 3. Table
        table_data = self.table_extractor.extract_from_raw_grid(
            headers=["Item", "Quantity", "Price", "InStock"],
            rows=[
                ["Widget A", 10, 19.99, True],
                ["Widget B", 25, 49.50, False],
            ],
            page_number=page_number,
            bbox_coords=(50.0, 180.0, 550.0, 300.0),
            page_width=page_width,
            page_height=page_height,
            caption="Inventory Summary",
        )
        nodes.append(
            ParsedASTNode(
                node_id=str(uuid.uuid4()),
                node_type="table",
                text_content=table_data.to_markdown(),
                bounding_box=table_data.bbox,
                confidence=0.92,
                table_data=table_data,
            )
        )

        return PageLayoutResult(page_number=page_number, nodes=nodes)

    def _parse_with_docling_native(
        self,
        pdf_path: str,
        start_from_page: int,
        total_pages: int | None,
        stop_event: threading.Event | None,
        on_page_complete: Callable[[PageLayoutResult], None] | None,
    ) -> Iterator[PageLayoutResult]:
        """Parse real PDF document using native Docling DocumentConverter."""
        conv_result = self._converter.convert(pdf_path)
        doc = conv_result.document

        # Pre-group docling items by page number
        items_by_page: dict[int, list[Any]] = {}
        if hasattr(doc, "iterate_items"):
            for item, _level in doc.iterate_items():
                prov = getattr(item, "prov", None)
                item_page = prov[0].page_no if prov and len(prov) > 0 else 1
                items_by_page.setdefault(item_page, []).append(item)

        # Iterate over pages in docling document
        page_count = len(doc.pages) if hasattr(doc, "pages") else (total_pages or 1)
        for page_idx in range(page_count):
            if stop_event is not None and stop_event.is_set():
                break

            if page_idx < start_from_page:
                continue

            page_num = page_idx + 1
            nodes: list[ParsedASTNode] = []

            # Extract body elements for this page
            for item in items_by_page.get(page_num, []):
                node_type = "paragraph"
                label = getattr(item, "label", "").lower()
                text = getattr(item, "text", "")

                if "heading" in label or "title" in label:
                    node_type = "heading_h1"
                    if not text.startswith("#"):
                        text = f"# {text}"
                elif "table" in label:
                    node_type = "table"
                elif "list" in label:
                    node_type = "list"

                # Normalize bounding box
                prov = getattr(item, "prov", None)
                box = getattr(prov[0], "bbox", None) if prov and len(prov) > 0 else None
                if box:
                    page_width = 612.0
                    page_height = 792.0
                    if hasattr(doc, "pages"):
                        try:
                            page_obj = (
                                doc.pages.get(page_num)
                                if isinstance(doc.pages, dict)
                                else doc.pages[page_num]
                            )
                            if page_obj and hasattr(page_obj, "size"):
                                page_width = float(page_obj.size.width)
                                page_height = float(page_obj.size.height)
                        except (KeyError, IndexError, AttributeError):
                            page_width = float(getattr(box, "coord_origin_width", 612.0))
                            page_height = float(getattr(box, "coord_origin_height", 792.0))
                    else:
                        page_width = float(getattr(box, "coord_origin_width", 612.0))
                        page_height = float(getattr(box, "coord_origin_height", 792.0))

                    coord_origin = getattr(box, "coord_origin", "BOTTOMLEFT")
                    origin = (
                        "bottom-left"
                        if str(coord_origin).upper().endswith("BOTTOMLEFT")
                        or str(coord_origin).upper() == "BOTTOMLEFT"
                        else "top-left"
                    )

                    norm_bbox = normalize_bbox(
                        x1=float(box.l),
                        y1=float(box.t),
                        x2=float(box.r),
                        y2=float(box.b),
                        page_width=page_width,
                        page_height=page_height,
                        origin=origin,
                        page_number=page_num,
                    )
                else:
                    norm_bbox = normalize_bbox(0, 0, 100, 100, 100, 100, page_number=page_num)

                table_data: TableData | None = None
                if node_type == "table":
                    headers: list[str] = []
                    rows: list[list[Any]] = []
                    grid = getattr(getattr(item, "data", None), "grid", None)
                    if grid:
                        headers = [getattr(c, "text", str(c)) for c in grid[0]]
                        rows = [[getattr(c, "text", str(c)) for c in row] for row in grid[1:]]
                    elif hasattr(item, "export_to_dataframe"):
                        try:
                            df = item.export_to_dataframe()
                            headers = [str(c) for c in df.columns]
                            rows = df.values.tolist()
                        except Exception:
                            pass

                    caption_text = ""
                    caption_obj = getattr(item, "caption", None)
                    if caption_obj:
                        caption_text = getattr(caption_obj, "text", str(caption_obj))

                    table_data = TableData(
                        headers=headers,
                        rows=rows,
                        bbox=norm_bbox,
                        page_number=page_num,
                        caption=caption_text,
                    )
                    if not text:
                        text = table_data.to_markdown()

                nodes.append(
                    ParsedASTNode(
                        node_id=str(uuid.uuid4()),
                        node_type=node_type,
                        text_content=text,
                        bounding_box=norm_bbox,
                        confidence=0.90,
                        table_data=table_data,
                    )
                )

            res = PageLayoutResult(page_number=page_num, nodes=nodes)

            if on_page_complete is not None:
                on_page_complete(res)

            yield res
            cleanup_vram_and_memory()
