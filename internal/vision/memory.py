"""VRAM and host memory lifecycle management for vision inference pipelines."""

import gc

from internal.config.settings import structlog


logger = structlog.get_logger(__name__)


def cleanup_vram_and_memory() -> None:
    """Proactively release PyTorch CUDA caches and run garbage collection.

    Prevents CUDA Out-Of-Memory (OOM) errors and host heap fragmentation across
    consecutive multi-page slice executions.
    """
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            if hasattr(torch.cuda, "ipc_collect"):
                torch.cuda.ipc_collect()
    except ImportError:
        pass
    except Exception as exc:
        logger.warning("vram_cleanup_warning", error=str(exc))

    gc.collect()
