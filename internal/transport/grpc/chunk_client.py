"""gRPC client protocol and streaming client scaffold for rag-chunk-lifecycle-service."""

from collections.abc import AsyncIterator, Sequence
from typing import Any, Protocol

from internal.config.settings import structlog


logger = structlog.get_logger(__name__)


class ChunkClientProtocol(Protocol):
    """Protocol for streaming AST nodes and audio transcripts over gRPC."""

    async def stream_document_ast(
        self,
        tenant_id: str,
        document_id: str,
        chunks_generator: AsyncIterator[Any] | Sequence[Any],
    ) -> bool:
        """Stream parsed AST document chunks to rag-chunk-lifecycle-service."""
        ...

    async def stream_audio_transcript(
        self,
        tenant_id: str,
        audio_id: str,
        segments_generator: AsyncIterator[Any] | Sequence[Any],
    ) -> bool:
        """Stream transcribed audio segments to rag-chunk-lifecycle-service."""
        ...


class ChunkServiceClient:
    """gRPC Client for streaming AST and audio transcripts to rag-chunk-lifecycle-service."""

    def __init__(self, target_endpoint: str = "rag-chunk-lifecycle-service:50051") -> None:
        self.target_endpoint = target_endpoint

    async def stream_document_ast(
        self,
        tenant_id: str,
        document_id: str,
        _chunks_generator: AsyncIterator[Any] | Sequence[Any],
    ) -> bool:
        """Stream AST nodes."""
        logger.info(
            "grpc_ast_streaming_started",
            target=self.target_endpoint,
            tenant_id=tenant_id,
            document_id=document_id,
        )
        return True

    async def stream_audio_transcript(
        self,
        tenant_id: str,
        audio_id: str,
        _segments_generator: AsyncIterator[Any] | Sequence[Any],
    ) -> bool:
        """Stream audio transcript chunks."""
        logger.info(
            "grpc_audio_streaming_started",
            target=self.target_endpoint,
            tenant_id=tenant_id,
            audio_id=audio_id,
        )
        return True
