from __future__ import annotations

import asyncio
import json
from typing import Any

import aio_pika
from aio_pika import Channel
from aio_pika.abc import AbstractIncomingMessage
from bson import ObjectId
from bson.errors import InvalidId
from loguru import logger

from app.config import Settings
from app.db import ads_collection, jobs_collection
from app.model.ad_model import AdModel
from app.model.job_model import JobModel
from app.mq import RETRY_COUNT_HEADER, declare_jobs_queue
from app.repository.ad_repo import AdRepository
from app.repository.job_repo import JobRepository
from app.service.meta_collector import MetaCollectorService
from app.util.shopify_util import is_shopify_domain


class WorkerLoop:
    def __init__(
        self,
        settings: Settings,
        job_repo: JobRepository | None = None,
        ad_repo: AdRepository | None = None,
        meta: MetaCollectorService | None = None,
    ) -> None:
        self._settings = settings
        self._job_repo = job_repo or JobRepository(jobs_collection(settings))
        self._ad_repo = ad_repo or AdRepository(ads_collection(settings))
        self._meta = meta or MetaCollectorService()
        self._stopping = False
        self._channel: Channel | None = None
        self._stop_event: asyncio.Event | None = None

    def stop(self) -> None:
        self._stopping = True
        if self._stop_event is not None and not self._stop_event.is_set():
            self._stop_event.set()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._close_channel())

    async def _close_channel(self) -> None:
        channel = self._channel
        if channel is None or channel.is_closed:
            return
        try:
            await channel.close()
        except Exception as exc:
            logger.debug("Channel close during shutdown: {}", exc)

    async def run_forever(self) -> None:
        self._stop_event = asyncio.Event()
        channel, queue = await declare_jobs_queue(self._settings)
        self._channel = channel
        logger.info(
            "Worker {} consuming queue={!r} max_retries={} "
            "retry_delay={}s dlq={!r}",
            self._settings.worker_id,
            self._settings.rabbitmq_queue,
            self._settings.rabbitmq_max_retries,
            self._settings.rabbitmq_retry_delay_seconds,
            self._settings.rabbitmq_dlq,
        )

        try:
            async with queue.iterator() as queue_iter:
                async for message in queue_iter:
                    if self._stopping:
                        break
                    await self._handle_message(message)
        except asyncio.CancelledError:
            logger.info("Worker cancelled")
            raise
        except Exception as exc:
            if self._stopping:
                logger.info("Worker stopped while consuming: {}", exc)
                return
            raise

    async def _handle_message(self, message: AbstractIncomingMessage) -> None:
        retry_count = self._get_retry_count(message)
        job: JobModel | None = None
        payload: dict[str, Any] | None = None

        try:
            payload = self._parse_payload(message.body)
            job = await self._resolve_job(payload)
            assert job.id is not None

            logger.info(
                "Received job {} attempt={}/{} keyword={!r} {} → {}",
                job.id,
                retry_count + 1,
                self._settings.rabbitmq_max_retries,
                job.keyword,
                job.start_date,
                job.end_date,
            )
            await self._process_job(job)
            await message.ack()
        except Exception as exc:
            logger.exception(
                "Job {} failed on attempt {}/{}: {}",
                getattr(job, "id", None),
                retry_count + 1,
                self._settings.rabbitmq_max_retries,
                exc,
            )
            await self._handle_failure(
                message,
                payload=payload,
                job=job,
                retry_count=retry_count,
                error=exc,
            )

    async def _handle_failure(
        self,
        message: AbstractIncomingMessage,
        *,
        payload: dict[str, Any] | None,
        job: JobModel | None,
        retry_count: int,
        error: Exception,
    ) -> None:
        can_retry = (
            job is not None
            and job.id is not None
            and payload is not None
            and retry_count + 1 < self._settings.rabbitmq_max_retries
        )
        if can_retry:
            assert job is not None and job.id is not None and payload is not None
            if self._stopping:
                await message.reject(requeue=True)
                return
            delay = self._settings.rabbitmq_retry_delay_seconds
            if delay > 0 and not await self._sleep_or_stop(delay):
                await message.reject(requeue=True)
                return
            await self._publish_retry(
                message,
                payload=payload,
                job_id=job.id,
                retry_count=retry_count + 1,
            )
            await message.ack()
            logger.warning(
                "Job {} requeued on {!r} for attempt {}/{}",
                job.id,
                self._settings.rabbitmq_queue,
                retry_count + 2,
                self._settings.rabbitmq_max_retries,
            )
            return

        if job is not None and job.id is not None:
            await self._job_repo.mark_failed(job.id, str(error))

        await message.reject(requeue=False)
        logger.error(
            "Job {} sent to DLQ after {} attempt(s)",
            getattr(job, "id", None),
            retry_count + 1,
        )

    async def _sleep_or_stop(self, delay: float) -> bool:
        """Sleep `delay` seconds. Returns False if shutdown was requested."""
        if self._stop_event is None:
            await asyncio.sleep(delay)
            return not self._stopping
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
            return False
        except asyncio.TimeoutError:
            return not self._stopping

    async def _publish_retry(
        self,
        message: AbstractIncomingMessage,
        *,
        payload: dict[str, Any],
        job_id: ObjectId,
        retry_count: int,
    ) -> None:
        if self._channel is None or self._channel.is_closed:
            raise RuntimeError("RabbitMQ channel is not available for retry publish")

        body_payload = dict(payload)
        body_payload["jobId"] = str(job_id)
        headers = dict(message.headers or {})
        headers[RETRY_COUNT_HEADER] = retry_count

        await self._channel.default_exchange.publish(
            aio_pika.Message(
                body=json.dumps(body_payload).encode("utf-8"),
                headers=headers,
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                content_type="application/json",
                correlation_id=message.correlation_id,
                message_id=message.message_id,
                type=message.type,
                app_id=message.app_id,
            ),
            routing_key=self._settings.rabbitmq_queue,
        )

    @staticmethod
    def _get_retry_count(message: AbstractIncomingMessage) -> int:
        raw = (message.headers or {}).get(RETRY_COUNT_HEADER, 0)
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            return 0

    def _parse_payload(self, body: bytes) -> dict[str, Any]:
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid RabbitMQ message body: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("RabbitMQ message must be a JSON object")
        return data

    async def _resolve_job(self, payload: dict[str, Any]) -> JobModel:
        job_id_raw = payload.get("jobId") or payload.get("_id")
        if job_id_raw:
            try:
                job_id = ObjectId(str(job_id_raw))
            except InvalidId as exc:
                raise ValueError(f"Invalid jobId: {job_id_raw}") from exc

            job = await self._job_repo.mark_running(job_id, self._settings.worker_id)
            if job is None:
                raise ValueError(f"Job not found: {job_id}")
            return job

        job = JobModel.model_validate(
            {
                "keyword": payload.get("keyword"),
                "startDate": payload.get("startDate"),
                "endDate": payload.get("endDate"),
                "country": payload.get("country") or self._settings.default_country,
                "maxResults": payload.get("maxResults"),
            }
        )
        return await self._job_repo.create_running(job, self._settings.worker_id)

    async def _process_job(self, job: JobModel) -> None:
        assert job.id is not None
        if job.start_date > job.end_date:
            raise ValueError(
                f"startDate {job.start_date} must be on or before endDate {job.end_date}"
            )

        max_results = job.max_results
        if max_results is None or max_results > self._settings.max_results_cap:
            max_results = self._settings.max_results_cap

        country = job.country or self._settings.default_country
        loop = asyncio.get_running_loop()
        saved = 0

        def on_ad(raw: dict[str, Any]) -> None:
            creative: dict[str, Any] | None = next(
                (
                    c
                    for c in raw.get("creatives", [])
                    if c.get("cta_type") == "SHOP_NOW" and c.get("link_url")
                ),
                None,
            )
            if creative is None:
                return

            link_url = creative.get("link_url")
            if not is_shopify_domain(link_url):
                return

            nonlocal saved
            ad_id = str(raw.get("id") or raw.get("ad_library_id") or "")
            if not ad_id:
                return

            ad = AdModel(
                job_id=job.id,
                ad_id=ad_id,
                keywords=[job.keyword],
                country=country,
                raw=raw,
            )
            asyncio.run_coroutine_threadsafe(
                self._ad_repo.upsert_one(ad),
                loop,
            ).result()
            saved += 1
            if saved == 1 or saved % 10 == 0:
                logger.info("Job {} saved {} ads so far", job.id, saved)

        await asyncio.to_thread(
            self._meta.search,
            keyword=job.keyword,
            start_date=job.start_date,
            end_date=job.end_date,
            country=country,
            max_results=max_results,
            on_ad=on_ad,
        )

        await self._job_repo.mark_done(job.id, ads_count=saved)
        logger.info("Job {} done — saved {} ads", job.id, saved)
