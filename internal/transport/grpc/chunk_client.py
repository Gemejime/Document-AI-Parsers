"""Streaming gRPC client for rag-chunk-lifecycle-service implementing DocumentAIChunkService."""

from collections.abc import AsyncIterable, AsyncIterator, Iterable, Sequence
from typing import Any, Protocol

import grpc

from api.proto.v1 import common_pb2, document_ai_pb2, document_ai_pb2_grpc
from internal.audio.models import AlignedTranscriptSegment
from internal.config.settings import settings, structlog
from internal.vision.layout.docling_engine import PageLayoutResult, ParsedASTNode


logger = structlog.get_logger(__name__)


BoundingBox: Any = getattr(common_pb2, "BoundingBox", None)
ASTNode: Any = getattr(document_ai_pb2, "ASTNode", None)
DocumentASTChunk: Any = getattr(document_ai_pb2, "DocumentASTChunk", None)
AudioTranscriptChunk: Any = getattr(document_ai_pb2, "AudioTranscriptChunk", None)
TranscriptSegment: Any = getattr(document_ai_pb2, "TranscriptSegment", None)
WordTimestamp: Any = getattr(document_ai_pb2, "WordTimestamp", None)
DocumentAIChunkServiceStub: Any = getattr(document_ai_pb2_grpc, "DocumentAIChunkServiceStub", None)


def transcript_segment_to_proto(seg: AlignedTranscriptSegment) -> Any:
    """Convert AlignedTranscriptSegment to Protobuf document_ai.TranscriptSegment."""
    word_protos = [
        WordTimestamp(
            word=w.word,
            start_time=w.start_time,
            end_time=w.end_time,
            confidence=w.confidence,
        )
        for w in seg.words
    ]
    return TranscriptSegment(
        speaker_id=seg.speaker_id,
        start_time_seconds=seg.start_time_seconds,
        end_time_seconds=seg.end_time_seconds,
        text=seg.text,
        words=word_protos,
    )


def audio_segments_to_chunk_proto(
    tenant_id: str,
    audio_id: str,
    segments: Sequence[AlignedTranscriptSegment],
) -> Any:
    """Convert list of AlignedTranscriptSegment to AudioTranscriptChunk."""
    proto_segs = [transcript_segment_to_proto(s) for s in segments]
    return AudioTranscriptChunk(
        tenant_id=tenant_id,
        audio_id=audio_id,
        segments=proto_segs,
    )


def ast_node_to_proto(node: ParsedASTNode) -> Any:
    """Convert ParsedASTNode to Protobuf document_ai.ASTNode with normalized BoundingBox."""
    bbox_proto = BoundingBox(
        x1=node.bounding_box.x1,
        y1=node.bounding_box.y1,
        x2=node.bounding_box.x2,
        y2=node.bounding_box.y2,
        page_number=node.bounding_box.page_number,
    )
    return ASTNode(
        node_id=node.node_id,
        node_type=node.node_type,
        text_content=node.text_content,
        bounding_box=bbox_proto,
        table_parquet_s3_uri=node.table_parquet_s3_uri or "",
    )


def page_result_to_chunk_proto(
    tenant_id: str,
    document_id: str,
    page_result: PageLayoutResult,
) -> Any:
    """Convert PageLayoutResult to Protobuf DocumentASTChunk."""
    proto_nodes = [ast_node_to_proto(n) for n in page_result.nodes]
    return DocumentASTChunk(
        tenant_id=tenant_id,
        document_id=document_id,
        page_number=page_result.page_number,
        nodes=proto_nodes,
    )


class ChunkClientProtocol(Protocol):
    """Protocol for streaming AST nodes and audio transcripts over gRPC."""

    async def stream_document_ast(
        self,
        tenant_id: str,
        document_id: str,
        chunks_generator: AsyncIterator[Any] | Sequence[Any] | Iterable[Any],
    ) -> bool:
        """Stream parsed AST document chunks to rag-chunk-lifecycle-service."""
        ...

    async def stream_audio_transcript(
        self,
        tenant_id: str,
        audio_id: str,
        segments_generator: AsyncIterator[Any] | Sequence[Any] | Iterable[Any],
        batch_size: int = 50,
    ) -> bool:
        """Stream transcribed audio segments to rag-chunk-lifecycle-service."""
        ...


class ChunkServiceClient:
    """gRPC client streaming AST manifests to rag-chunk-lifecycle-service."""

    def __init__(
        self,
        target_endpoint: str | None = None,
        timeout_seconds: float = 5.0,
        stub: Any = None,
    ) -> None:
        self.target_endpoint = target_endpoint or settings.CHUNK_SERVICE_ENDPOINT
        self.timeout_seconds = timeout_seconds
        self.stub = stub

    async def _generate_chunks(
        self,
        tenant_id: str,
        document_id: str,
        pages_or_chunks: AsyncIterator[Any] | Sequence[Any] | Iterable[Any],
    ) -> AsyncIterator[Any]:
        """Convert input items to DocumentASTChunk protobufs asynchronously."""
        if isinstance(pages_or_chunks, AsyncIterable):
            async for item in pages_or_chunks:
                if isinstance(item, PageLayoutResult):
                    yield page_result_to_chunk_proto(tenant_id, document_id, item)
                else:
                    yield item
        else:
            for item in pages_or_chunks:
                if isinstance(item, PageLayoutResult):
                    yield page_result_to_chunk_proto(tenant_id, document_id, item)
                else:
                    yield item

    async def stream_document_ast(
        self,
        tenant_id: str,
        document_id: str,
        chunks_generator: AsyncIterator[Any] | Sequence[Any] | Iterable[Any],
    ) -> bool:
        """Stream AST nodes over gRPC StreamDocumentAST RPC meeting <= 1.5s manifest egress SLO."""
        logger.info(
            "grpc_ast_streaming_started",
            target=self.target_endpoint,
            tenant_id=tenant_id,
            document_id=document_id,
        )

        chunk_count = 0
        chunks_to_send: list[Any] = []

        async for chunk in self._generate_chunks(tenant_id, document_id, chunks_generator):
            chunk_count += 1
            chunks_to_send.append(chunk)

        # Injected stub (e.g. testing or local servicer)
        if self.stub is not None:

            async def req_iterator() -> AsyncIterator[Any]:
                for c in chunks_to_send:
                    yield c

            response = await self.stub.StreamDocumentAST(
                req_iterator(), timeout=self.timeout_seconds
            )
            return bool(getattr(response, "acknowledged", True))

        # Explicit mock/testing target endpoint
        if self.target_endpoint in ("mock", "test", "disabled") or not self.target_endpoint:
            logger.info("grpc_mock_streaming_completed", chunks_count=chunk_count)
            return True

        try:
            # Attempt real gRPC connection with timeout
            async with grpc.aio.insecure_channel(self.target_endpoint) as channel:
                stub = DocumentAIChunkServiceStub(channel)

                async def request_iterator() -> AsyncIterator[Any]:
                    for c in chunks_to_send:
                        yield c

                call = stub.StreamDocumentAST(request_iterator(), timeout=self.timeout_seconds)
                response = await call

                if response.acknowledged:
                    logger.info(
                        "grpc_ast_streaming_acknowledged",
                        tenant_id=tenant_id,
                        document_id=document_id,
                        chunks_sent=chunk_count,
                        chunks_received=response.chunks_received,
                    )
                    return True
                return False
        except grpc.RpcError as rpc_err:
            logger.error(
                "grpc_endpoint_rpc_error",
                target=self.target_endpoint,
                code=rpc_err.code().name if hasattr(rpc_err, "code") else "UNKNOWN",
                chunks_buffered=chunk_count,
            )
            raise
        except Exception as exc:
            logger.error(
                "grpc_streaming_error",
                target=self.target_endpoint,
                error=str(exc),
                chunks_buffered=chunk_count,
            )
            raise

    async def stream_audio_transcript(
        self,
        tenant_id: str,
        audio_id: str,
        segments_generator: AsyncIterator[Any] | Sequence[Any] | Iterable[Any],
        batch_size: int = 50,
    ) -> bool:
        """Stream transcribed audio segments over gRPC StreamAudioTranscript RPC."""
        logger.info(
            "grpc_audio_streaming_started",
            target=self.target_endpoint,
            tenant_id=tenant_id,
            audio_id=audio_id,
        )

        all_segments: list[Any] = []
        if isinstance(segments_generator, AsyncIterable):
            async for item in segments_generator:
                all_segments.append(item)
        else:
            for item in segments_generator:
                all_segments.append(item)

        # Batch segments into AudioTranscriptChunks
        chunks_to_send: list[Any] = []
        if all_segments:
            if isinstance(all_segments[0], AudioTranscriptChunk):
                chunks_to_send.extend(all_segments)
            else:
                for i in range(0, len(all_segments), batch_size):
                    batch = all_segments[i : i + batch_size]
                    chunk = audio_segments_to_chunk_proto(tenant_id, audio_id, batch)
                    chunks_to_send.append(chunk)

        chunk_count = len(chunks_to_send)

        # Injected stub (e.g. testing)
        if self.stub is not None:

            async def req_iterator() -> AsyncIterator[Any]:
                for c in chunks_to_send:
                    yield c

            response = await self.stub.StreamAudioTranscript(
                req_iterator(), timeout=self.timeout_seconds
            )
            return bool(getattr(response, "acknowledged", True))

        # Explicit mock/testing target endpoint
        if self.target_endpoint in ("mock", "test", "disabled") or not self.target_endpoint:
            logger.info("grpc_mock_audio_streaming_completed", chunks_count=chunk_count)
            return True

        try:
            async with grpc.aio.insecure_channel(self.target_endpoint) as channel:
                stub = DocumentAIChunkServiceStub(channel)

                async def request_iterator() -> AsyncIterator[Any]:
                    for c in chunks_to_send:
                        yield c

                call = stub.StreamAudioTranscript(request_iterator(), timeout=self.timeout_seconds)
                response = await call

                if response.acknowledged:
                    logger.info(
                        "grpc_audio_streaming_acknowledged",
                        tenant_id=tenant_id,
                        audio_id=audio_id,
                        chunks_sent=chunk_count,
                        chunks_received=response.chunks_received,
                    )
                    return True
                return False
        except grpc.RpcError as rpc_err:
            logger.error(
                "grpc_audio_endpoint_rpc_error",
                target=self.target_endpoint,
                code=rpc_err.code().name if hasattr(rpc_err, "code") else "UNKNOWN",
                chunks_buffered=chunk_count,
            )
            raise
        except Exception as exc:
            logger.error(
                "grpc_audio_streaming_error",
                target=self.target_endpoint,
                error=str(exc),
                chunks_buffered=chunk_count,
            )
            raise
