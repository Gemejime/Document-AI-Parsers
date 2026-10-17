"""Dual-mode VRAM saturation and memory soak testing suite."""

import gc
import os
import time
import tracemalloc

import pytest

from internal.audio.memory import cleanup_vram_and_memory as audio_cleanup_memory
from internal.vision.layout.docling_engine import DoclingEngine
from internal.vision.memory import cleanup_vram_and_memory as vision_cleanup_memory


def test_ci_regression_heap_memory_stability() -> None:
    """CI Regression Mode: 50-100 rapid cycles asserting zero memory drift (delta < 1%).

    Tracks host heap allocations via tracemalloc and Python object counts across
    50 iterations with cleanup_vram_and_memory invoked between runs.
    """
    engine = DoclingEngine(use_native=False)

    # Warmup cycle
    _ = list(engine.parse_slice_pages(pdf_path="warmup.pdf", total_pages=5))
    vision_cleanup_memory()
    audio_cleanup_memory()
    gc.collect()

    tracemalloc.start()
    initial_current, _ = tracemalloc.get_traced_memory()
    initial_objects = len(gc.get_objects())

    cycles = 50
    for i in range(cycles):
        # Process multi-page mock documents
        pages = list(engine.parse_slice_pages(pdf_path=f"doc_cycle_{i}.pdf", total_pages=10))
        assert len(pages) == 10

        # Memory lifecycle cleanup
        vision_cleanup_memory()
        audio_cleanup_memory()

    gc.collect()
    final_current, peak = tracemalloc.get_traced_memory()
    final_objects = len(gc.get_objects())
    tracemalloc.stop()

    # Memory drift should be bounded (< 1% relative to peak or negligible absolute growth < 500 KB)
    drift_bytes = max(0, final_current - initial_current)
    drift_ratio = drift_bytes / max(peak, 1)
    object_delta = abs(final_objects - initial_objects)

    assert drift_ratio < 0.01 or drift_bytes < 512 * 1024, (
        f"Host heap drifted by {drift_bytes} bytes ({drift_ratio:.2%}) over {cycles} cycles"
    )
    assert object_delta < 200, f"Uncollected Python objects drifted by {object_delta}"


@pytest.mark.skipif(
    os.environ.get("RUN_EXTENDED_SOAK_TEST") != "true",
    reason="Extended soak testing requires GPU hardware and RUN_EXTENDED_SOAK_TEST=true environment variable",
)
def test_extended_vram_soak_stress() -> None:
    """Extended Soak Mode: Stress parsing on GPU hardware, validating zero CUDA OOM errors.

    Runs continuous parsing up to target soak duration (default: 6 hours, or SOAK_DURATION_SECONDS),
    proactively asserting torch.cuda.empty_cache() keeps VRAM within limits.
    """
    import torch

    assert torch.cuda.is_available(), "CUDA GPU must be accessible for extended soak test"

    soak_duration = float(os.environ.get("SOAK_DURATION_SECONDS", 21600))  # 6 hours = 21,600s
    engine = DoclingEngine(use_native=True)
    start_time = time.time()
    iterations = 0

    while time.time() - start_time < soak_duration:
        iterations += 1
        # Execute stress iteration
        pages = list(engine.parse_slice_pages(pdf_path="stress_target.pdf", total_pages=20))
        assert len(pages) == 20

        vision_cleanup_memory()

        # Verify CUDA memory remains bounded and not leaking
        allocated = torch.cuda.memory_allocated()
        max_allocated = torch.cuda.max_memory_allocated()
        assert allocated < 8 * (1024**3), f"Allocated VRAM exceeded 8 GiB threshold: {allocated}"
        assert max_allocated < 16 * (1024**3), f"Peak VRAM exceeded 16 GiB cap: {max_allocated}"

    assert iterations > 0
