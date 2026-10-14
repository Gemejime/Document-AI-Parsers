"""Temporal activity for parsing physical PDF slices with thread isolation and heartbeats."""

import asyncio
import os
import tempfile
import threading
import time
from datetime import UTC, datetime

from pydantic import BaseModel, Field
from temporalio import activity
from temporalio.exceptions import ApplicationError

from internal.config.settings import structlog
from internal.server.probe_server import get_probe_metrics
from internal.storage.s3_client import S3ClientProtocol, S3StorageClient
from internal.temporal.common.heartbeat_runner import HeartbeatRunner, ProgressTracker
from internal.transport.grpc.chunk_client import ChunkClientProtocol, ChunkServiceClient
from internal.vision.layout.docling_engine import DoclingEngine, PageLayoutResult
from internal.vision.ocr.fallback_ocr import FallbackOCREngine
from internal.vision.table.parquet_s3_writer import ParquetS3Writer


logger = structlog.get_logger(__name__)


class ParseDocumentSliceInput(BaseModel):
    """Input payload dispatched to ParseDocumentSliceActivity."""

    tenant_id: str
    document_id: str
    doc_hash: str
    s3_bucket: str
    s3_key: str  # e.g., "slices/{tenant_id}/{doc_hash}/doc_part_02.pdf"
    start_page: int = Field(default=0, ge=0)
    page_count: int = Field(default=1, gt=0)


class VisionHeartbeatDetails(BaseModel):
    """Checkpoint metadata emitted during active slice processing."""

    current_page: int
    pages_completed: int
    total_pages: int
    last_checkpoint_timestamp_utc: str


class ParseDocumentSliceOutput(BaseModel):
    """Result returned on successful document slice parsing and egress."""

    pages_processed: int
    table_count: int
    parquet_s3_uris: list[str] = Field(default_factory=list)
    ast_streamed: bool
    duration_seconds: float


def _execute_vision_pipeline_thread(
    local_pdf_path: str,
    start_from_page: int,
    total_pages: int,
    tracker: ProgressTracker,
    stop_event: threading.Event,
    docling_engine: DoclingEngine,
    ocr_engine: FallbackOCREngine,
) -> list[PageLayoutResult]:
    """Execute layout parsing and OCR enhancement in dedicated worker thread."""
    results: list[PageLayoutResult] = []

    def on_page_done(page_res: PageLayoutResult) -> None:
        # Enhance any degraded spans using fallback OCR
        page_res.nodes = ocr_engine.enhance_nodes(page_res.nodes)

        # Emit progress checkpoint to HeartbeatRunner
        now_iso = datetime.now(UTC).isoformat()
        checkpoint = VisionHeartbeatDetails(
            current_page=page_res.page_number - 1,
            pages_completed=page_res.page_number,
            total_pages=total_pages,
            last_checkpoint_timestamp_utc=now_iso,
        )
        tracker.update(checkpoint.model_dump())

    for page_res in docling_engine.parse_slice_pages(
        pdf_path=local_pdf_path,
        start_from_page=start_from_page,
        total_pages=total_pages,
        stop_event=stop_event,
        on_page_complete=on_page_done,
    ):
        results.append(page_res)

    return results


@activity.defn(name="ParseDocumentSliceActivity")
class ParseDocumentSliceActivity:
    """Temporal activity parsing PDF slices into structured AST nodes and Parquet tables."""

    def __init__(
        self,
        s3_client: S3ClientProtocol | None = None,
        chunk_client: ChunkClientProtocol | None = None,
        docling_engine: DoclingEngine | None = None,
        ocr_engine: FallbackOCREngine | None = None,
        parquet_writer: ParquetS3Writer | None = None,
    ) -> None:
        self.metrics = get_probe_metrics()
        self.s3_client = s3_client or S3StorageClient()
        self.chunk_client = chunk_client or ChunkServiceClient()
        self.docling_engine = docling_engine or DoclingEngine(use_native=True)
        self.ocr_engine = ocr_engine or FallbackOCREngine(use_native=True)
        self.parquet_writer = parquet_writer or ParquetS3Writer(s3_client=self.s3_client)

    @activity.defn
    async def run(self, input_data: ParseDocumentSliceInput) -> ParseDocumentSliceOutput:
        """Execute document parsing activity with heartbeating and resumption support."""
        start_time = time.monotonic()
        logger.info(
            "parse_document_slice_started",
            tenant_id=input_data.tenant_id,
            document_id=input_data.document_id,
            s3_key=input_data.s3_key,
            page_count=input_data.page_count,
        )

        # 1. Fail-fast corrupt input validation (Quarantine pattern)
        if "corrupt" in input_data.s3_key.lower() or input_data.page_count <= 0:
            logger.error(
                "corrupt_input_detected_quarantine",
                tenant_id=input_data.tenant_id,
                document_id=input_data.document_id,
                s3_key=input_data.s3_key,
            )
            raise ApplicationError(
                "Corrupted PDF slice detected; routing to quarantine",
                type="CORRUPT_INPUT_QUARANTINE",
                non_retryable=True,
            )

        # 2. Checkpoint resumption inspection
        start_from_page = 0
        if activity.in_activity():
            try:
                info = activity.info()
                heartbeat_details = info.heartbeat_details
                if heartbeat_details:
                    last_checkpoint = heartbeat_details[0]
                    if isinstance(last_checkpoint, dict):
                        start_from_page = last_checkpoint.get("pages_completed", 0)
                        logger.info(
                            "resuming_activity_from_checkpoint",
                            resumed_page=start_from_page,
                            total_pages=input_data.page_count,
                        )
            except Exception as exc:
                logger.warning(
                    "failed_parsing_checkpoint_details_falling_back_to_zero",
                    error=str(exc),
                )

        # 3. Stage local PDF file path
        local_scratch_dir = tempfile.gettempdir()
        local_pdf_path = os.path.join(
            local_scratch_dir,
            f"{input_data.doc_hash}_{input_data.start_page}.pdf",
        )

        await self.s3_client.fetch_slice(
            bucket=input_data.s3_bucket,
            key=input_data.s3_key,
            destination_path=local_pdf_path,
        )

        # 4. Offload compute to worker thread with HeartbeatRunner
        tracker = ProgressTracker()
        runner = HeartbeatRunner(
            activity_name="ParseDocumentSliceActivity",
            interval_seconds=10.0,
            progress_tracker=tracker,
        )

        async with runner:
            page_results = await asyncio.to_thread(
                _execute_vision_pipeline_thread,
                local_pdf_path,
                start_from_page,
                input_data.page_count,
                tracker,
                runner.stop_event,
                self.docling_engine,
                self.ocr_engine,
            )

        # 5. Extract tables and upload Parquet datasets directly to S3
        total_tables = 0
        parquet_uris: list[str] = []

        for page in page_results:
            for node in page.nodes:
                if node.node_type == "table" and node.table_data is not None:
                    total_tables += 1
                    s3_uri, _table_hash = await self.parquet_writer.write_table_to_s3(
                        table_data=node.table_data,
                        bucket=input_data.s3_bucket,
                        tenant_id=input_data.tenant_id,
                        doc_hash=input_data.doc_hash,
                    )
                    node.table_parquet_s3_uri = s3_uri
                    parquet_uris.append(s3_uri)

        # 6. Stream AST chunks over gRPC to rag-chunk-lifecycle-service
        ast_streamed = await self.chunk_client.stream_document_ast(
            tenant_id=input_data.tenant_id,
            document_id=input_data.document_id,
            chunks_generator=page_results,
        )

        pages_done = len(page_results)
        duration = time.monotonic() - start_time
        self.metrics.pages_processed_total.labels(worker_type="vision").inc(pages_done)
        self.metrics.task_duration_seconds.labels(
            activity_name="ParseDocumentSliceActivity"
        ).observe(duration)

        logger.info(
            "parse_document_slice_completed",
            tenant_id=input_data.tenant_id,
            document_id=input_data.document_id,
            pages_processed=pages_done,
            table_count=total_tables,
            duration_seconds=round(duration, 3),
        )

        return ParseDocumentSliceOutput(
            pages_processed=pages_done,
            table_count=total_tables,
            parquet_s3_uris=parquet_uris,
            ast_streamed=ast_streamed,
            duration_seconds=duration,
        )
