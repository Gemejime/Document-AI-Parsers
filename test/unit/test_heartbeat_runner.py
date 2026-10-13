"""Unit tests for thread-safe HeartbeatRunner, cooperative cancellation, and GIL isolation."""

import asyncio
import threading
import time

import pytest

from internal.temporal.common.heartbeat_runner import HeartbeatRunner, ProgressTracker


def test_progress_tracker_thread_safety() -> None:
    """Verify concurrent updates to ProgressTracker maintain state integrity."""
    tracker = ProgressTracker(initial_details={"page": 0})
    iterations = 500

    def worker_thread() -> None:
        for i in range(iterations):
            tracker.update({"page": i + 1})

    threads = [threading.Thread(target=worker_thread) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    details = tracker.get_details()
    assert isinstance(details, dict)
    assert details["page"] == iterations


def test_cooperative_cancellation_signal() -> None:
    """Verify cooperative cancellation sets stop_event across thread boundary."""
    tracker = ProgressTracker()
    assert not tracker.should_stop()
    assert not tracker.stop_event.is_set()

    tracker.request_stop()
    assert tracker.should_stop()
    assert tracker.stop_event.is_set()


@pytest.mark.asyncio
async def test_heartbeat_runner_emits_steady_heartbeats() -> None:
    """Verify HeartbeatRunner emits checkpoints at configured intervals."""
    emitted_checkpoints: list[dict[str, int]] = []

    def mock_heartbeat_fn(details: dict[str, int] | None = None) -> None:
        if details is not None:
            emitted_checkpoints.append(details)

    tracker = ProgressTracker(initial_details={"page": 1})
    runner = HeartbeatRunner(
        activity_name="TestActivity",
        interval_seconds=0.04,
        progress_tracker=tracker,
        heartbeat_fn=mock_heartbeat_fn,
    )

    async with runner:
        await asyncio.sleep(0.06)
        tracker.update({"page": 2})
        await asyncio.sleep(0.06)

    assert len(emitted_checkpoints) >= 2
    assert emitted_checkpoints[-1]["page"] == 2


@pytest.mark.asyncio
async def test_gil_isolation_under_blocking_thread_compute() -> None:
    """Verify heartbeats continue ticking while a worker thread executes."""
    heartbeat_ticks = 0

    def mock_heartbeat_fn(*_args: object) -> None:
        nonlocal heartbeat_ticks
        heartbeat_ticks += 1

    tracker = ProgressTracker()
    runner = HeartbeatRunner(
        activity_name="GilStressActivity",
        interval_seconds=0.03,
        progress_tracker=tracker,
        heartbeat_fn=mock_heartbeat_fn,
    )

    def cpu_heavy_worker(stop_evt: threading.Event) -> int:
        """Simulate CPU / C-extension worker loop in background thread."""
        accum = 0
        end_time = time.monotonic() + 0.12
        while time.monotonic() < end_time and not stop_evt.is_set():
            time.sleep(0.01)
            accum += 1
        return accum

    async with runner:
        result = await asyncio.to_thread(cpu_heavy_worker, runner.stop_event)

    assert result > 0
    # The event loop should have run unblocked, allowing at least 2 ticks
    assert heartbeat_ticks >= 2
