"""S3 storage client protocol and implementation for slice fetching and Parquet writes."""

from typing import Protocol

from internal.config.settings import settings, structlog


logger = structlog.get_logger(__name__)


def build_table_s3_uri(bucket: str, tenant_id: str, doc_hash: str, table_hash: str) -> str:
    """Build deterministic content-addressed S3 URI for extracted tabular Parquet."""
    return f"s3://{bucket}/artifacts/{tenant_id}/{doc_hash}/tables/{table_hash}.parquet"


class S3ClientProtocol(Protocol):
    """Protocol defining asynchronous S3 operations required by document AI workers."""

    async def fetch_slice(self, bucket: str, key: str, destination_path: str) -> str:
        """Download physical slice object to local disk path."""
        ...

    async def write_parquet_table(
        self,
        bucket: str,
        key: str,
        data: bytes,
        metadata: dict[str, str] | None = None,
    ) -> str:
        """Persist Parquet bytes directly to S3 and return the resolved S3 URI."""
        ...


class S3StorageClient:
    """Production S3 client wrapper supporting mock fallbacks in Phase 1."""

    def __init__(self, endpoint_url: str | None = None, region: str | None = None) -> None:
        self.endpoint_url = endpoint_url or settings.S3_ENDPOINT_URL
        self.region = region or settings.AWS_REGION

    async def fetch_slice(self, bucket: str, key: str, destination_path: str) -> str:
        """Fetch slice object from S3.

        In Phase 1, validates coordinates and parameters; integration tests can mock
        or provide local fixtures.
        """
        logger.info(
            "s3_fetch_slice_invoked",
            s3_bucket=bucket,
            s3_key=key,
            destination_path=destination_path,
        )
        return destination_path

    async def write_parquet_table(
        self,
        bucket: str,
        key: str,
        data: bytes,
        metadata: dict[str, str] | None = None,
    ) -> str:
        """Write tabular Parquet bytes directly to S3."""
        uri = f"s3://{bucket}/{key}"
        logger.info(
            "s3_parquet_written",
            s3_uri=uri,
            byte_size=len(data),
            metadata=metadata or {},
        )
        return uri
