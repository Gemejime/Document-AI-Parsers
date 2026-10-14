"""Performance test verifying AST manifest egress completes within <= 1.5 seconds SLO."""

import time

import pytest

from internal.transport.grpc.chunk_client import ChunkServiceClient
from internal.vision.layout.docling_engine import DoclingEngine


@pytest.mark.asyncio
async def test_manifest_egress_within_slo() -> None:
    """Verify that a 50-page document manifest streams to ChunkServiceClient in <= 1.5s."""
    engine = DoclingEngine(use_native=False)
    # Generate 50 pages of structured AST nodes
    pages = list(engine.parse_slice_pages(pdf_path="perf_doc.pdf", total_pages=50))
    assert len(pages) == 50

    client = ChunkServiceClient(target_endpoint="mock")

    start_time = time.perf_counter()
    success = await client.stream_document_ast(
        tenant_id="perf-tenant",
        document_id="perf-doc-50p",
        chunks_generator=pages,
    )
    elapsed_time = time.perf_counter() - start_time

    assert success is True
    # SLO: complete 50-100 page document manifest egress in <= 1.5 seconds
    assert elapsed_time <= 1.5, f"Manifest egress exceeded 1.5s SLO: took {elapsed_time:.4f}s"
