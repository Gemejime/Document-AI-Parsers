"""Performance test measuring AST manifest protobuf conversion time."""

import time

import pytest

from internal.transport.grpc.chunk_client import ChunkServiceClient
from internal.vision.layout.docling_engine import DoclingEngine


@pytest.mark.asyncio
async def test_manifest_protobuf_conversion_time() -> None:
    """Benchmark verifying that a 50-page document manifest converts to protobuf in <= 5.0s."""
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
    # Benchmark: convert 50-page document manifest to protobuf within 5.0s on standard CPU runtime
    assert elapsed_time <= 5.0, (
        f"Protobuf manifest conversion exceeded 5.0s: took {elapsed_time:.4f}s"
    )
