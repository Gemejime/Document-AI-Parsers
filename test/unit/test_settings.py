"""Unit tests for configuration settings and secured logging redaction."""

import os
from unittest.mock import patch

from internal.config.settings import (
    WorkerSettings,
    _scrub_value,
    configure_logging,
    secured_logging_filter,
)


def test_worker_settings_defaults() -> None:
    """Verify default values comply with production architecture requirements."""
    cfg = WorkerSettings()
    assert cfg.ENVIRONMENT == "development"
    assert cfg.TEMPORAL_HOST == "localhost:7233"
    assert cfg.TEMPORAL_NAMESPACE == "default"
    assert cfg.VISION_TASK_QUEUE == "doc-ai-vision-queue"
    assert cfg.AUDIO_TASK_QUEUE == "doc-ai-audio-queue"
    assert cfg.PROBE_PORT == 8080
    assert cfg.HEARTBEAT_INTERVAL_SECONDS == 10.0
    assert cfg.HEARTBEAT_TIMEOUT_SECONDS == 30.0
    assert cfg.VRAM_THRESHOLD_BYTES == 20 * 1024 * 1024 * 1024
    assert cfg.MODEL_CACHE_DIR == "/models/cache"
    assert cfg.HF_HOME == "/models/cache/huggingface"
    assert cfg.TORCH_HOME == "/models/cache/torch"


def test_worker_settings_env_override() -> None:
    """Verify settings pick up environment overrides."""
    with patch.dict(
        os.environ,
        {
            "ENVIRONMENT": "production",
            "TEMPORAL_HOST": "temporal.prod:7233",
            "PROBE_PORT": "9090",
            "HEARTBEAT_INTERVAL_SECONDS": "5.0",
        },
    ):
        cfg = WorkerSettings()
        assert cfg.ENVIRONMENT == "production"
        assert cfg.TEMPORAL_HOST == "temporal.prod:7233"
        assert cfg.PROBE_PORT == 9090
        assert cfg.HEARTBEAT_INTERVAL_SECONDS == 5.0


def test_secured_logging_redacts_auth_and_tokens() -> None:
    """Verify security invariant: tokens, secrets, and auth headers are redacted."""
    raw_event = {
        "event": "user_action",
        "authorization": "Bearer super-secret-jwt-token",
        "api_key": "secret-12345",
        "nested": {
            "token": "my-access-token",
            "password": "plain-text-pwd",
        },
    }

    filtered = secured_logging_filter(None, "info", raw_event)

    assert filtered["authorization"] == "[REDACTED]"
    assert filtered["api_key"] == "[REDACTED]"
    assert filtered["nested"]["token"] == "[REDACTED]"
    assert filtered["nested"]["password"] == "[REDACTED]"


def test_secured_logging_redacts_document_and_transcript_payloads() -> None:
    """Verify security invariant: zero customer document text or transcripts logged."""
    raw_event = {
        "event": "page_parsed",
        "text_content": "Sensitive medical records or confidential financial tables",
        "ocr_text": "Customer personal identification data",
        "transcript": "Audio recording transcript with speech PII",
        "safe_meta": {"page_number": 12, "tenant_id": "tenant-abc"},
    }

    filtered = secured_logging_filter(None, "info", raw_event)

    assert filtered["text_content"] == "[REDACTED]"
    assert filtered["ocr_text"] == "[REDACTED]"
    assert filtered["transcript"] == "[REDACTED]"
    assert filtered["safe_meta"]["page_number"] == 12
    assert filtered["safe_meta"]["tenant_id"] == "tenant-abc"


def test_scrub_presigned_s3_url() -> None:
    """Verify that presigned S3 URLs have authentication queries masked."""
    presigned_url = (
        "https://my-bucket.s3.amazonaws.com/doc.pdf"
        "?X-Amz-Signature=abcdef123456&X-Amz-Credential=AKIAIOSFODNN7EXAMPLE"
    )
    scrubbed = _scrub_value("s3_url", presigned_url)

    assert "abcdef123456" not in scrubbed
    assert "AKIAIOSFODNN7EXAMPLE" not in scrubbed
    assert "[REDACTED]" in scrubbed


def test_configure_logging_idempotence() -> None:
    """Verify configure_logging executes cleanly without crashing."""
    cfg = WorkerSettings(ENVIRONMENT="development")
    configure_logging(cfg)

    prod_cfg = WorkerSettings(ENVIRONMENT="production")
    configure_logging(prod_cfg)


def test_worker_settings_validation_constraints() -> None:
    """Verify validation bounds on OCR_CONFIDENCE_THRESHOLD and TABLE_PARQUET_COMPRESSION."""
    import pytest
    from pydantic import ValidationError

    # Valid bounds
    valid_cfg = WorkerSettings(
        OCR_CONFIDENCE_THRESHOLD=0.5,
        TABLE_PARQUET_COMPRESSION="zstd",
    )
    assert valid_cfg.OCR_CONFIDENCE_THRESHOLD == 0.5
    assert valid_cfg.TABLE_PARQUET_COMPRESSION == "zstd"

    # Out-of-bounds confidence threshold
    with pytest.raises(ValidationError):
        WorkerSettings(OCR_CONFIDENCE_THRESHOLD=-0.1)

    with pytest.raises(ValidationError):
        WorkerSettings(OCR_CONFIDENCE_THRESHOLD=1.5)

    # Invalid parquet compression codec
    with pytest.raises(ValidationError):
        WorkerSettings.model_validate({"TABLE_PARQUET_COMPRESSION": "invalid_codec"})
