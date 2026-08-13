from __future__ import annotations

import asyncio
import json
import threading
from datetime import date, timedelta
from typing import Any

import aio_pika
from aio_pika import Channel
from aio_pika.abc import AbstractIncomingMessage
from bson import ObjectId
from bson.errors import InvalidId
from loguru import logger
from app.ai_client import AIClient
from app.config import Settings
from app.db import (
    ads_collection,
    jobs_collection,
    products_collection,
    shops_collection,
)
from app.model.ad_model import AdModel
from app.model.job_model import JobModel, JobType
from app.mq import RETRY_COUNT_HEADER, declare_jobs_queue
from app.repository.ad_repo import AdRepository
from app.repository.job_repo import JobRepository
from app.repository.product_repo import ProductRepository
from app.repository.shop_repo import ShopRepository
from app.service.meta_collector import MetaCollectorService, ReachThresholdReached
from app.util.shopify_util import (
    extract_domain,
    get_product_from_url,
    is_shopify_domain,
)

DOMAIN_WINDOW_DAYS = 30
MAX_LEVEL_SEARCH = 3


class WorkerLoop:
    def __init__(
        self,
        settings: Settings,
        analyzer: AIClient,
        job_repo: JobRepository | None = None,
        ad_repo: AdRepository | None = None,
        product_repo: ProductRepository | None = None,
        shop_repo: ShopRepository | None = None,
        meta: MetaCollectorService | None = None,
    ) -> None:
        self._settings = settings
        self._job_repo = job_repo or JobRepository(jobs_collection(settings))
        self._ad_repo = ad_repo or AdRepository(ads_collection(settings))
        self._product_repo = product_repo or ProductRepository(
            products_collection(settings)
        )
        self._shop_repo = shop_repo or ShopRepository(
            shops_collection(settings), settings
        )
        self._meta = meta or MetaCollectorService()
        self._stopping = False
        self._channel: Channel | None = None
        self._stop_event: asyncio.Event | None = None
        self._analyzer = analyzer

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
            "Worker {} consuming queue={!r} max_retries={} " "retry_delay={}s dlq={!r}",
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
                "Received job {} attempt={}/{} keyword={!r} type={} {} → {}",
                job.id,
                retry_count + 1,
                self._settings.rabbitmq_max_retries,
                job.keyword,
                job.type,
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
                "type": payload.get("type") or JobType.KEYWORD,
                "startDate": payload.get("startDate"),
                "endDate": payload.get("endDate"),
                "country": payload.get("country") or self._settings.default_country,
                "maxResults": payload.get("maxResults")
                or self._settings.max_results_cap,
                "statusFilter": payload.get("statusFilter") or "ALL",
                "mustReach": payload.get("mustReach") or 0,
                "storeMustReach": payload.get("storeMustReach") or 0,
                "metadata": payload.get("metadata"),
                "mediaType": payload.get("mediaType"),
            }
        )
        return await self._job_repo.create_running(job, self._settings.worker_id)

    async def _publish_job(self, job_id: ObjectId) -> None:
        if self._channel is None or self._channel.is_closed:
            raise RuntimeError("RabbitMQ channel is not available for job publish")

        await self._channel.default_exchange.publish(
            aio_pika.Message(
                body=json.dumps({"jobId": str(job_id)}).encode("utf-8"),
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                content_type="application/json",
            ),
            routing_key=self._settings.rabbitmq_queue,
        )

    @staticmethod
    def _parent_job_id(metadata: dict[str, Any] | None) -> ObjectId | None:
        if not metadata:
            return None
        raw = metadata.get("parentJobId")
        if not raw:
            return None
        try:
            return ObjectId(str(raw))
        except InvalidId:
            return None

    @staticmethod
    def _existing_qualified_count(existing: JobModel) -> int:
        if existing.qualified_ads_count is not None:
            return int(existing.qualified_ads_count)
        if existing.ads_count is not None:
            return int(existing.ads_count)
        return 0

    async def _spawn_domain_jobs(
        self,
        parent: JobModel,
        rootId: ObjectId,
        domains: set[str],
    ) -> None:
        if not domains:
            return

        assert parent.id is not None
        end = date.today()
        start = end - timedelta(days=DOMAIN_WINDOW_DAYS - 1)

        for domain in sorted(domains):
            existing = await self._job_repo.find_overlapping_domain_job(
                domain, start, end
            )
            if existing is not None:
                count = self._existing_qualified_count(existing)
                if count > 0:
                    await self._ad_repo.update_ads_qualified(
                        parent_id=parent.id, count=count, shop=domain
                    )
                await self._shop_repo.update_qualified_ads_count(
                    domain,
                    count,
                    job_id=existing.id,
                    parent_job_id=parent.id,
                )
                logger.info(
                    "Reuse domain job {} keyword={!r} qualifiedAdsCount={} "
                    "→ parent {} (+{})",
                    existing.id,
                    domain,
                    count,
                    parent.id,
                    count,
                )
                # continue

            child = JobModel(
                keyword=domain,
                type=JobType.DOMAIN,
                start_date=start,
                end_date=end,
                country=parent.country,
                max_results=parent.max_results,
                user_id=parent.user_id,
                status_filter=parent.status_filter,
                must_reach=parent.must_reach,
                media_type=parent.media_type,
                metadata={"parentJobId": str(parent.id)},
                rootId=rootId,
                primary_keyword=parent.keyword,
            )
            child = await self._job_repo.create_pending(child)
            assert child.id is not None
            await self._shop_repo.upsert_for_domain(
                domain,
                child.id,
                parent_job_id=parent.id,
                root_job_id=rootId,
            )
            await self._publish_job(child.id)
            logger.info(
                "Spawned domain job {} keyword={!r} {} → {} from parent {}",
                child.id,
                domain,
                start,
                end,
                parent.id,
            )

    async def _publish_keyword_progress(
        self,
        job_id: ObjectId,
        *,
        ads_count: int,
        store_count: int,
    ) -> None:
        await self._job_repo.update_progress(
            job_id,
            ads_count=ads_count,
            store_count=store_count,
        )

    async def _search_window(
        self,
        *,
        job: JobModel,
        start_date: date,
        end_date: date,
        country: str,
        max_results: int,
        seen_ad_ids: set[str],
        pending_domains: set[str] | None,
        collect_domains: bool,
        spawn_domains_immediately: bool = False,
    ) -> tuple[int, int]:
        assert job.id is not None
        loop = asyncio.get_running_loop()
        new_saved = 0
        keywords: list[str] = []
        must_reach = int(job.must_reach or 0)
        store_must_reach = int(job.store_must_reach or 0)
        spawned_domains: set[str] = set()
        # Meta collector runs on_ad on a ThreadPoolExecutor (max 4 workers).
        # Guard all shared mutable state and check-then-act spawn/upsert logic.
        state_lock = threading.Lock()
        # Maps each product URL to its best position in Ads Library's impression sort.
        product_ranks: dict[str, int] = {}
        should_analyze = (
            spawn_domains_immediately and job.level and job.level < MAX_LEVEL_SEARCH
        )
        # Jobs without an explicit root are roots themselves.
        rootId = job.rootId or job.id

        def on_ad(raw: dict[str, Any], link_ads_url: str, impression_rank: int) -> None:
            nonlocal new_saved
            if not (raw.get("cta_type") == "SHOP_NOW" and raw.get("link_url")):
                return

            link_url = raw.get("link_url")
            if not isinstance(link_url, str) or not link_url:
                return

            link_domain = extract_domain(link_url)
            if not link_domain:
                return

            with state_lock:
                is_new_domain = False
                if collect_domains:
                    if not is_shopify_domain(link_url):
                        return
                    if pending_domains is not None:
                        is_new_domain = link_domain not in pending_domains
                        pending_domains.add(link_domain)
                    shop = link_domain
                else:
                    target = job.keyword.strip().lower().rstrip(".")
                    if link_domain != target and not link_domain.endswith(f".{target}"):
                        return
                    shop = target

                should_spawn = (
                    spawn_domains_immediately
                    and is_new_domain
                    and link_domain not in spawned_domains
                )
                if should_spawn:
                    spawned_domains.add(link_domain)

                ad_id = str(raw.get("ad_archive_id") or raw.get("id") or "")
                is_duplicate_ad = not ad_id or ad_id in seen_ad_ids
                if not is_duplicate_ad:
                    seen_ad_ids.add(ad_id)
                    new_saved += 1
                    if should_analyze:
                        result_keyword = self._analyzer._llm_analyze_keyword(
                            title=raw.get("title"), content=raw.get("body")
                        )
                        if result_keyword:
                            keywords.append(result_keyword)

                total = len(seen_ad_ids)
                store_total = len(pending_domains) if pending_domains is not None else 0
                refresh_store_only = (
                    is_duplicate_ad
                    and collect_domains
                    and pending_domains is not None
                    and is_new_domain
                )

            if should_spawn:
                asyncio.run_coroutine_threadsafe(
                    self._spawn_domain_jobs(job, rootId, {link_domain}),
                    loop,
                ).result()
                logger.info(
                    "Job {} discovered store {!r} — spawned domain job " "(stores={})",
                    job.id,
                    link_domain,
                    store_total,
                )

            if is_duplicate_ad:
                if refresh_store_only:
                    asyncio.run_coroutine_threadsafe(
                        self._publish_keyword_progress(
                            job.id,
                            ads_count=total,
                            store_count=store_total,
                        ),
                        loop,
                    ).result()
                return

            ad = AdModel(
                job_ids=[job.id, rootId],
                ad_id=ad_id,
                keywords=[job.keyword],
                country=country,
                raw=raw,
                shop=shop,
            )
            asyncio.run_coroutine_threadsafe(
                self._ad_repo.upsert_one(ad),
                loop,
            ).result()

            try:
                product_result = get_product_from_url(link_url)
                if product_result is not None:
                    product_url, product = product_result
                    product = asyncio.run_coroutine_threadsafe(
                        self._product_repo.upsert_one(
                            product=product,
                            job_ids=[job.id, rootId],
                            link_url=product_url,
                            shop=shop,
                            link_ads_url=link_ads_url,
                            keywords=[
                                (
                                    job.keyword
                                    if spawn_domains_immediately
                                    else job.primary_keyword
                                )
                            ],
                        ),
                        loop,
                    ).result()
                    with state_lock:
                        current_rank = product_ranks.get(product_url)
                        if current_rank is None or impression_rank < current_rank:
                            product_ranks[product_url] = impression_rank

            except Exception as exc:
                logger.warning("Could not collect product from {!r}: {}", link_url, exc)

            asyncio.run_coroutine_threadsafe(
                self._publish_keyword_progress(
                    job.id,
                    ads_count=total,
                    store_count=store_total,
                ),
                loop,
            ).result()

            if total == 1 or total % 10 == 0 or is_new_domain:
                logger.info(
                    "Job {} progress — ads={} stores={}",
                    job.id,
                    total,
                    store_total,
                )

            if must_reach > 0 and total > must_reach:
                raise ReachThresholdReached(f"ads {total} > mustReach {must_reach}")
            if (
                collect_domains
                and store_must_reach > 0
                and store_total > store_must_reach
            ):
                raise ReachThresholdReached(
                    f"stores {store_total} > storeMustReach {store_must_reach}"
                )

        fetched = await asyncio.to_thread(
            self._meta.search,
            keyword=job.keyword,
            start_date=start_date,
            end_date=end_date,
            country=country,
            media_type=job.media_type,
            max_results=max_results,
            on_ad=on_ad,
            status_filter=job.status_filter,
        )

        if should_analyze:
            exsting_keywords = await self._job_repo.get_existing_keyword(rootId)
            group_keyword = self._analyzer._llm_group_keywords(
                keywords=keywords, existing_keywords=exsting_keywords
            )
            for keyword in group_keyword:
                await self._spawn_child_keyword_jobs(
                    parent=job,
                    keyword=keyword,
                    rootId=rootId,
                ),
        if not spawn_domains_immediately and product_ranks:
            await self._update_sumary_count(
                product_ranks=product_ranks, store_ads=new_saved
            )
        return new_saved, fetched

    async def _process_keyword_job(
        self,
        job: JobModel,
        *,
        country: str,
        max_results: int,
    ) -> tuple[int, int]:
        pending_domains: set[str] = set()
        seen_ad_ids: set[str] = set()

        await self._search_window(
            job=job,
            start_date=job.start_date,
            end_date=job.end_date,
            country=country,
            max_results=max_results,
            seen_ad_ids=seen_ad_ids,
            pending_domains=pending_domains,
            collect_domains=True,
            spawn_domains_immediately=True,
        )

        await self._publish_keyword_progress(
            job.id,
            ads_count=len(seen_ad_ids),
            store_count=len(pending_domains),
        )
        return len(seen_ad_ids), len(pending_domains)

    async def _process_domain_job(
        self,
        job: JobModel,
        *,
        country: str,
        max_results: int,
    ) -> int:
        seen_ad_ids: set[str] = set()
        await self._search_window(
            job=job,
            start_date=job.start_date,
            end_date=job.end_date,
            country=country,
            max_results=max_results,
            seen_ad_ids=seen_ad_ids,
            pending_domains=None,
            collect_domains=False,
        )
        return len(seen_ad_ids)

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
        is_domain = str(job.type) == JobType.DOMAIN.value

        if is_domain:
            saved = await self._process_domain_job(
                job, country=country, max_results=max_results
            )
            await self._job_repo.mark_done(
                job.id,
                ads_count=saved,
                qualified_ads_count=saved,
            )
            parent_id = self._parent_job_id(job.metadata)
            if parent_id is not None and saved > 0:
                shop = job.keyword
                await self._ad_repo.update_ads_qualified(
                    parent_id=parent_id, count=saved, shop=shop
                )
                await self._shop_repo.update_qualified_ads_count(
                    shop,
                    saved,
                    job_id=job.id,
                    parent_job_id=parent_id,
                )
                logger.info(
                    "Domain job {} qualifiedAdsCount={} → parent {} shop={!r}",
                    job.id,
                    saved,
                    parent_id,
                    shop,
                )
        else:
            saved, store_count = await self._process_keyword_job(
                job, country=country, max_results=max_results
            )
            await self._job_repo.mark_done(
                job.id,
                ads_count=saved,
                store_count=store_count,
            )
            logger.info(
                "Job {} done — saved {} ads, storeCount={}",
                job.id,
                saved,
                store_count,
            )
            return

        logger.info("Job {} done — saved {} ads", job.id, saved)

    async def _spawn_child_keyword_jobs(
        self,
        parent: JobModel,
        rootId: str,
        keyword: str,
    ) -> None:
        assert parent.id is not None
        job = JobModel(
            level=parent.level + 1,
            rootId=ObjectId(rootId),
            keyword=keyword,
            parentId=ObjectId(parent.id),
            country=parent.country,
            media_type=parent.media_type,
            start_date=parent.start_date,
            end_date=parent.end_date,
            max_results=parent.max_results,
            store_must_reach=parent.store_must_reach,
            type=JobType.KeywordExpand,
            user_id=parent.user_id,
            status_filter=parent.status_filter,
            must_reach=parent.must_reach,
            metadata={"parentJobId": str(parent.id)},
        )
        child = await self._job_repo.create_pending(job)
        assert child.id is not None
        await self._publish_job(child.id)

    async def _update_sumary_count(
        self, product_ranks: dict[str, int], store_ads: int
    ) -> None:
        for product_url, impression_rank in product_ranks.items():
            active_ads_count = await self._ad_repo.count_active_by_product_url(
                product_url
            )
            await self._product_repo.update_ad_counts(
                link_url=product_url,
                store_ads_count=store_ads,
                active_ads_count=active_ads_count,
                highest_impression_rank=impression_rank,
            )
