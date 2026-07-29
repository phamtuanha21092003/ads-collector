from __future__ import annotations

import asyncio
import signal
import sys

from loguru import logger

from app.config import get_settings
from app.db import close_client, system_configs_collection
from app.mq import close as close_mq
from app.repository.system_config_repo import SystemConfigRepository
from app.service.meta_collector import MetaCollectorService
from app.service.proxy_rotation import ProxyRotationService
from app.worker.loop import WorkerLoop


def _configure_logging() -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level="INFO",
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan> - "
            "<level>{message}</level>"
        ),
    )


async def _async_main() -> None:
    settings = get_settings()
    if not settings.db_uri:
        raise SystemExit("DB_URI is required")
    if not settings.rabbitmq_uri:
        raise SystemExit("RABBITMQ_URI is required")

    proxy_rotation = ProxyRotationService()
    await proxy_rotation.load_from_mongo(
        SystemConfigRepository(system_configs_collection(settings))
    )
    meta = MetaCollectorService(proxy_pool=proxy_rotation.pool)
    worker = WorkerLoop(settings, meta=meta)
    worker_task = asyncio.create_task(worker.run_forever(), name="ads-collector-worker")
    loop = asyncio.get_running_loop()

    def _handle_stop() -> None:
        if worker_task.done():
            return
        logger.info("Shutdown signal received")
        worker.stop()
        worker_task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_stop)
        except NotImplementedError:
            signal.signal(sig, lambda *_: _handle_stop())

    try:
        await worker_task
    except asyncio.CancelledError:
        logger.info("Shutdown complete")
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.remove_signal_handler(sig)
            except (NotImplementedError, ValueError):
                pass
        await close_mq()
        await close_client()
        logger.info("Worker stopped")


def main() -> None:
    _configure_logging()
    try:
        asyncio.run(_async_main())
    except KeyboardInterrupt:
        # Fallback if signal handler is unavailable.
        logger.info("Interrupted")


if __name__ == "__main__":
    main()
