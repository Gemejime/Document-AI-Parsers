"""gRPC transport client package."""

from internal.transport.grpc.chunk_client import (
    ChunkClientProtocol,
    ChunkServiceClient,
)


__all__ = ["ChunkClientProtocol", "ChunkServiceClient"]
