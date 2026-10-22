"""Worker configuration settings and secured structured logging."""

import logging
import os
import re
from collections.abc import MutableMapping
from typing import Any, Literal, cast

import structlog
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from structlog.types import EventDict, WrappedLogger


class WorkerSettings(BaseSettings):
    """Runtime configuration for rag-document-ai-worker compute pools."""

    model_config = SettingsConfigDict(
        env_file=os.getenv("ENV_FILE", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    ENVIRONMENT: str = Field(
        default="development",
        description="Deployment environment (development, staging, production)",
    )
    TEMPORAL_HOST: str = Field(
        default="localhost:7233",
        description="Temporal frontend endpoint host:port",
    )
    TEMPORAL_NAMESPACE: str = Field(
        default="default",
        description="Temporal namespace for workflow and activity execution",
    )
    VISION_TASK_QUEUE: str = Field(
        default="doc-ai-vision-queue",
        description="Task queue dedicated to doc-ai-vision-worker activities",
    )
    AUDIO_TASK_QUEUE: str = Field(
        default="doc-ai-audio-queue",
        description="Task queue dedicated to doc-ai-audio-worker activities",
    )
    PROBE_HOST: str = Field(
        default="0.0.0.0",
        description="Network interface for FastAPI probe server",
    )
    PROBE_PORT: int = Field(
        default=8080,
        description="HTTP port for OpenAPI 3.1 documentation and Kubernetes probes",
    )
    MODEL_CACHE_DIR: str = Field(
        default="/models/cache",
        description="Base read-only volume directory mounted for pre-baked models",
    )
    HF_HOME: str = Field(
        default="/models/cache/huggingface",
        description="Hugging Face models and hub cache location",
    )
    TORCH_HOME: str = Field(
        default="/models/cache/torch",
        description="PyTorch weights cache directory",
    )
    DOCLING_CACHE_DIR: str = Field(
        default="/models/cache/docling",
        description="Docling artifact cache directory",
    )
    WHISPER_CACHE_DIR: str = Field(
        default="/models/cache/whisper",
        description="Faster-Whisper model cache directory",
    )
    PPOCR_HOME: str = Field(
        default="/tmp/.paddleocr",
        description="PaddleOCR model artifacts directory (writable in read-only containers)",
    )
    HEARTBEAT_INTERVAL_SECONDS: float = Field(
        default=10.0,
        description="Interval in seconds between active Temporal activity heartbeats",
    )
    HEARTBEAT_TIMEOUT_SECONDS: float = Field(
        default=30.0,
        description="Temporal activity heartbeat timeout buffer against GC/jitter",
    )
    VRAM_THRESHOLD_BYTES: int = Field(
        default=20 * 1024 * 1024 * 1024,
        description="GPU VRAM threshold ceiling in bytes (default 20 GiB)",
    )
    OCR_CONFIDENCE_THRESHOLD: float = Field(
        default=0.80,
        description="Confidence threshold triggering secondary OCR pass",
        ge=0.0,
        le=1.0,
    )
    TABLE_PARQUET_COMPRESSION: Literal["snappy", "zstd", "gzip"] = Field(
        default="snappy",
        description="Compression codec for PyArrow Parquet tables (snappy, zstd, gzip)",
    )
    S3_ENDPOINT_URL: str | None = Field(
        default=None,
        description="Optional custom S3 endpoint URL (for LocalStack/MinIO testing)",
    )
    AWS_REGION: str = Field(
        default="us-east-1",
        description="AWS region for S3 bucket operations",
    )
    AWS_ACCESS_KEY_ID: str | None = Field(
        default=None,
        description="Optional AWS access key ID for S3 operations",
    )
    AWS_SECRET_ACCESS_KEY: str | None = Field(
        default=None,
        description="Optional AWS secret access key for S3 operations",
    )
    WHISPER_MODEL_SIZE: str = Field(
        default="large-v3",
        description="Faster-Whisper model size variant (tiny, base, small, medium, large-v3)",
    )
    WHISPER_DEVICE: str = Field(
        default="cuda",
        description="Inference device for Faster-Whisper (cuda, cpu)",
    )
    WHISPER_COMPUTE_TYPE: str = Field(
        default="float16",
        description="Quantization and compute type for Faster-Whisper (float16, int8_float16, float32)",
    )
    WHISPER_BEAM_SIZE: int = Field(
        default=5,
        description="Beam search size for Whisper decoding",
    )
    PYANNOTE_MODEL_ID: str = Field(
        default="pyannote/speaker-diarization-3.1",
        description="Hugging Face model repository ID for PyAnnote speaker diarization",
    )
    HF_TOKEN: str | None = Field(
        default=None,
        description="Hugging Face authentication token for gated model repositories",
    )
    AUDIO_SAMPLE_RATE: int = Field(
        default=16000,
        description="Standard audio sample rate in Hz for Whisper and PyAnnote",
    )
    AUDIO_PROGRESS_STEP_SECONDS: float = Field(
        default=30.0,
        description="Audio window interval chunk size in seconds for progressive processing",
    )
    CHUNK_SERVICE_ENDPOINT: str = Field(
        default="rag-chunk-lifecycle-service:50051",
        description="Downstream gRPC chunk lifecycle endpoint for streaming ASTs and transcripts",
    )


# Global singleton settings
settings = WorkerSettings()

# Sensitive query params and token scrub patterns
_REDACTED_TEXT = "[REDACTED]"
_SENSITIVE_KEY_PATTERNS = [
    re.compile(r"auth(orization)?", re.IGNORECASE),
    re.compile(r"bearer", re.IGNORECASE),
    re.compile(r"token", re.IGNORECASE),
    re.compile(r"secret", re.IGNORECASE),
    re.compile(r"password", re.IGNORECASE),
    re.compile(r"api[_-]?key", re.IGNORECASE),
    re.compile(r"access[_-]?key", re.IGNORECASE),
    re.compile(r"x-amz-signature", re.IGNORECASE),
    re.compile(r"x-amz-credential", re.IGNORECASE),
    re.compile(r"x-amz-security-token", re.IGNORECASE),
]

_RESTRICTED_PAYLOAD_KEYS = {
    "text_content",
    "ocr_text",
    "transcript",
    "raw_text",
    "words",
    "customer_payload",
}

_URL_QUERY_SENSITIVE_PARAM_REGEX = re.compile(
    r"(X-Amz-Signature|X-Amz-Credential|X-Amz-Security-Token|access_token|token|key)=[^&\s]+",
    re.IGNORECASE,
)


def _scrub_value(key: str, value: Any) -> Any:
    """Recursively redact sensitive tokens or document text content."""
    key_str = key

    # 1. Reject customer document and transcript contents per invariant
    if key_str.lower() in _RESTRICTED_PAYLOAD_KEYS:
        return _REDACTED_TEXT

    # 2. Check sensitive auth/token key patterns
    if any(pat.search(key_str) for pat in _SENSITIVE_KEY_PATTERNS):
        return _REDACTED_TEXT

    # 3. Check for presigned S3 URLs or embedded tokens in string values
    if isinstance(value, str):
        if "X-Amz-" in value or "token=" in value:
            return _URL_QUERY_SENSITIVE_PARAM_REGEX.sub(r"\1=[REDACTED]", value)
        return value

    # 4. Traverse dictionaries recursively
    if isinstance(value, MutableMapping):
        return {k: _scrub_value(str(k), v) for k, v in value.items()}

    # 5. Traverse lists recursively
    if isinstance(value, list | tuple):
        return [_scrub_value(key, v) for v in value]

    return value


def secured_logging_filter(
    _logger: WrappedLogger, _method_name: str, event_dict: EventDict
) -> EventDict:
    """Structlog processor ensuring zero PII, auth tokens, or document text leak."""
    for key in list(event_dict.keys()):
        event_dict[key] = _scrub_value(key, event_dict[key])
    return event_dict


def configure_logging(custom_settings: WorkerSettings | None = None) -> None:
    """Configure structured JSON logging for production or console for development."""
    cfg = custom_settings or settings

    # Propagate model cache and cloud environment variables to runtime libraries
    os.environ["HF_HOME"] = cfg.HF_HOME
    os.environ["TORCH_HOME"] = cfg.TORCH_HOME
    os.environ["DOCLING_CACHE_DIR"] = cfg.DOCLING_CACHE_DIR
    os.environ["WHISPER_CACHE_DIR"] = cfg.WHISPER_CACHE_DIR
    os.environ["PPOCR_HOME"] = cfg.PPOCR_HOME
    os.environ["AWS_REGION"] = cfg.AWS_REGION
    os.environ["AWS_DEFAULT_REGION"] = cfg.AWS_REGION
    if cfg.AWS_ACCESS_KEY_ID:
        os.environ["AWS_ACCESS_KEY_ID"] = cfg.AWS_ACCESS_KEY_ID
    if cfg.AWS_SECRET_ACCESS_KEY:
        os.environ["AWS_SECRET_ACCESS_KEY"] = cfg.AWS_SECRET_ACCESS_KEY
    if cfg.S3_ENDPOINT_URL:
        os.environ["S3_ENDPOINT_URL"] = cfg.S3_ENDPOINT_URL
    if cfg.HF_TOKEN:
        os.environ["HF_TOKEN"] = cfg.HF_TOKEN

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        secured_logging_filter,
    ]

    is_production = cfg.ENVIRONMENT.lower() in ("production", "staging")

    if is_production:
        formatter_processor: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        formatter_processor = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    handler = logging.StreamHandler()
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=formatter_processor,
            foreign_pre_chain=shared_processors,
        )
    )

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO if is_production else logging.DEBUG)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Obtain a structured logger bound with standard schema helpers."""
    return cast("structlog.stdlib.BoundLogger", structlog.get_logger(name))
