"""Direct S3 Parquet serialization for extracted tabular datasets."""

import io

import pyarrow.parquet as pq

from internal.config.settings import settings, structlog
from internal.storage.s3_client import S3ClientProtocol, S3StorageClient
from internal.vision.table.table_extractor import TableData


logger = structlog.get_logger(__name__)


class ParquetS3Writer:
    """Serializes TableData to Snappy Parquet bytes and writes directly to S3."""

    def __init__(self, s3_client: S3ClientProtocol | None = None) -> None:
        self.s3_client = s3_client or S3StorageClient()

    def serialize_to_parquet_bytes(
        self,
        table_data: TableData,
        compression: str | None = None,
    ) -> bytes:
        """Serialize TableData into Parquet bytes with Snappy compression."""
        codec = compression or settings.TABLE_PARQUET_COMPRESSION
        pa_table = table_data.to_pyarrow_table()

        sink = io.BytesIO()
        pq.write_table(pa_table, sink, compression=codec)
        return sink.getvalue()

    async def write_table_to_s3(
        self,
        table_data: TableData,
        bucket: str,
        tenant_id: str,
        doc_hash: str,
    ) -> tuple[str, str]:
        """Write TableData directly to S3 as content-addressed Parquet dataset.

        Returns:
            Tuple of (s3_uri, table_hash).
        """
        table_hash = table_data.compute_table_hash()
        s3_key = f"artifacts/{tenant_id}/{doc_hash}/tables/{table_hash}.parquet"
        parquet_bytes = self.serialize_to_parquet_bytes(table_data)

        metadata = {
            "x-amz-meta-tenant-id": tenant_id,
            "x-amz-meta-doc-hash": doc_hash,
            "x-amz-meta-table-hash": table_hash,
            "x-amz-meta-page-number": str(table_data.page_number),
            "Content-Type": "application/vnd.apache.parquet",
        }

        s3_uri = await self.s3_client.write_parquet_table(
            bucket=bucket,
            key=s3_key,
            data=parquet_bytes,
            metadata=metadata,
        )

        logger.info(
            "parquet_table_persisted_to_s3",
            tenant_id=tenant_id,
            doc_hash=doc_hash,
            table_hash=table_hash,
            page_number=table_data.page_number,
            byte_size=len(parquet_bytes),
            s3_uri=s3_uri,
        )

        return s3_uri, table_hash
