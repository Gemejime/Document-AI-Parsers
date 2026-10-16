# rag-document-ai-worker

Horizontally scalable, stateless multimodal document and audio AI parser worker.

## Overview

`rag-document-ai-worker` is a decoupled compute worker microservice built on Temporal.io, FastAPI, PyTorch, Docling, PaddleOCR, and Whisper. It provides high-throughput document slicing, layout decomposition, OCR fallback, tabular data extraction, and audio transcription with diarization for RAG ingestion pipelines.

## Compute Worker Pools

- **Vision Worker (`doc-ai-vision-worker`)**: Slices document PDFs, analyzes layout hierarchies (Docling / PaddleOCR), and emits AST manifests and Parquet tables.
- **Audio Worker (`doc-ai-audio-worker`)**: Ingests audio windows, performs Whisper transcription and Pyannote diarization, and streams word-level aligned transcripts.

## Development

```bash
# Install development dependencies
pip install -e .[dev,vision,audio]

# Run tests
pytest test/unit test/integration

# Code formatting and linting
python -m ruff check --fix .
python -m ruff format .
python -m mypy cmd internal test
```
