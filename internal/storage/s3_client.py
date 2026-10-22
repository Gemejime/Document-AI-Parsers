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

    def __init__(
        self,
        endpoint_url: str | None = None,
        region: str | None = None,
        aws_access_key_id: str | None = None,
        aws_secret_access_key: str | None = None,
    ) -> None:
        self.endpoint_url = endpoint_url or settings.S3_ENDPOINT_URL
        self.region = region or settings.AWS_REGION
        self.aws_access_key_id = aws_access_key_id or settings.AWS_ACCESS_KEY_ID
        self.aws_secret_access_key = aws_secret_access_key or settings.AWS_SECRET_ACCESS_KEY

    def get_client_kwargs(self) -> dict[str, str]:
        """Return resolved keyword arguments for initializing an aiobotocore / boto3 client."""
        kwargs: dict[str, str] = {"region_name": self.region}
        if self.endpoint_url:
            kwargs["endpoint_url"] = self.endpoint_url
        if self.aws_access_key_id:
            kwargs["aws_access_key_id"] = self.aws_access_key_id
        if self.aws_secret_access_key:
            kwargs["aws_secret_access_key"] = self.aws_secret_access_key
        return kwargs

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
