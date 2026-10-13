"""Thread-isolated background Temporal activity heartbeat runner with cooperative cancellation."""

import asyncio
import contextlib
import threading
import time
from collections.abc import Callable
from types import TracebackType
from typing import Any

from temporalio import activity
from temporalio.exceptions import CancelledError

from internal.config.settings import structlog
from internal.server.probe_server import get_probe_metrics


logger = structlog.get_logger(__name__)


class ProgressTracker:
    """Thread-safe state container updated by model inference worker threads."""

    def __init__(self, initial_details: Any = None) -> None:
        self._lock = threading.Lock()
        self._details: Any = initial_details
        self.stop_event = threading.Event()

    def update(self, details: Any) -> None:
        """Update current progress details under lock."""
        with self._lock:
            self._details = details

    def get_details(self) -> Any:
        """Retrieve latest progress details under lock."""
        with self._lock:
            return self._details

    def request_stop(self) -> None:
        """Signal cooperative cancellation to worker thread."""
        self.stop_event.set()

    def should_stop(self) -> bool:
        """Check if worker thread should terminate early."""
        return self.stop_event.is_set()


class HeartbeatRunner:
    """Background asyncio task emitting Temporal activity heartbeats with checkpoint details."""

    def __init__(
        self,
        activity_name: str,
        interval_seconds: float = 10.0,
        progress_tracker: ProgressTracker | None = None,
        heartbeat_fn: Callable[..., None] | None = None,
    ) -> None:
        self.activity_name = activity_name
        self.interval_seconds = interval_seconds
        self.tracker = progress_tracker or ProgressTracker()
        self._heartbeat_fn = heartbeat_fn
        self._task: asyncio.Task[None] | None = None
        self._stop_requested = asyncio.Event()
        self._metrics = get_probe_metrics()

    @property
    def stop_event(self) -> threading.Event:
        """Return threading event for cooperative cancellation across thread boundaries."""
        return self.tracker.stop_event

    def update_progress(self, details: Any) -> None:
        """Convenience method to update underlying tracker details."""
        self.tracker.update(details)

    async def _emit_heartbeat_tick(self, last_tick_time: float) -> float:
        """Emit a single heartbeat checkpoint and record latency metrics."""
        now = time.monotonic()
        delay = now - last_tick_time
        self._metrics.heartbeat_delay_seconds.labels(activity_name=self.activity_name).observe(
            delay
        )

        details = self.tracker.get_details()

        try:
            if self._heartbeat_fn is not None:
                if details is not None:
                    self._heartbeat_fn(details)
                else:
                    self._heartbeat_fn()
            elif activity.in_activity():
                if details is not None:
                    activity.heartbeat(details)
                else:
                    activity.heartbeat()

            self._metrics.heartbeat_emissions_total.labels(activity_name=self.activity_name).inc()
            logger.debug(
                "temporal_heartbeat_emitted",
                activity_name=self.activity_name,
                delay_seconds=round(delay, 3),
            )
        except (CancelledError, asyncio.CancelledError):
            logger.warning(
                "temporal_activity_cancelled_during_heartbeat",
                activity_name=self.activity_name,
            )
            self.tracker.request_stop()
            raise
        except Exception as exc:
            logger.error(
                "temporal_heartbeat_emission_failed",
                activity_name=self.activity_name,
                error=str(exc),
            )
            raise

        return now

    async def _run_loop(self) -> None:
        """Continuous ticker emitting heartbeats at configured intervals."""
        last_tick_time = time.monotonic()
        while not self._stop_requested.is_set():
            try:
                await asyncio.sleep(self.interval_seconds)
                if self._stop_requested.is_set():
                    break
                last_tick_time = await self._emit_heartbeat_tick(last_tick_time)
            except (asyncio.CancelledError, CancelledError):
                self.tracker.request_stop()
                break

    async def start(self) -> None:
        """Start the background heartbeat task."""
        self._stop_requested.clear()
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """Stop and await background heartbeat task."""
        self._stop_requested.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, CancelledError):
                await self._task
            self._task = None

    async def __aenter__(self) -> "HeartbeatRunner":
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.stop()
