"""Bootstrap entry point for doc-ai-audio-worker compute pool."""

import asyncio
import contextlib
import signal
import sys
from typing import Any

import uvicorn
from temporalio.client import Client
from temporalio.worker import Worker

from internal.config.settings import configure_logging, get_logger, settings
from internal.server.probe_server import create_probe_app, set_probe_ready
from internal.temporal.activities.audio_activity import TranscribeAudioWindowActivity


logger = get_logger(__name__)


async def run_worker() -> None:
    """Initialize Temporal audio worker and ASGI probe server concurrently."""
    configure_logging(settings)
    logger.info(
        "bootstrapping_audio_worker",
        environment=settings.ENVIRONMENT,
        temporal_host=settings.TEMPORAL_HOST,
        task_queue=settings.AUDIO_TASK_QUEUE,
        probe_port=settings.PROBE_PORT,
    )

    # 1. Initialize ASGI probe server
    probe_app = create_probe_app(worker_type="audio")
    server_config = uvicorn.Config(
        app=probe_app,
        host=settings.PROBE_HOST,
        port=settings.PROBE_PORT,
        log_config=None,
        access_log=False,
    )
    server = uvicorn.Server(server_config)

    # 2. Connect to Temporal cluster
    try:
        temporal_client = await Client.connect(
            settings.TEMPORAL_HOST,
            namespace=settings.TEMPORAL_NAMESPACE,
        )
        logger.info("connected_to_temporal", host=settings.TEMPORAL_HOST)
    except Exception as exc:
        logger.error("temporal_connection_failed", error=str(exc))
        # Keep server running so /ready correctly reports 503 while retrying
        await server.serve()
        return

    activity_instance = TranscribeAudioWindowActivity()
    worker = Worker(
        temporal_client,
        task_queue=settings.AUDIO_TASK_QUEUE,
        activities=[activity_instance.run],
    )

    # 3. Mark worker as ready
    set_probe_ready(True, worker_type="audio")

    # 4. Handle signals
    loop = asyncio.get_running_loop()

    def handle_shutdown(*_args: Any) -> None:
        logger.info("shutdown_signal_received")
        set_probe_ready(False, worker_type="audio")
        server.should_exit = True

    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, handle_shutdown)

    # 5. Concurrently run HTTP probe server and Temporal Activity Worker
    logger.info("audio_worker_polling_active", task_queue=settings.AUDIO_TASK_QUEUE)
    await asyncio.gather(
        server.serve(),
        worker.run(),
    )


def main() -> None:
    """Synchronous process entry point."""
    try:
        asyncio.run(run_worker())
    except (KeyboardInterrupt, SystemExit):
        logger.info("audio_worker_shutdown_complete")
        sys.exit(0)


if __name__ == "__main__":
    main()
