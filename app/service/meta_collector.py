from __future__ import annotations

import random
import requests
import time
from collections.abc import Callable
from datetime import date, datetime
from datetime import time as dt_time
from typing import Any
from concurrent.futures import Future, ThreadPoolExecutor
import os

from loguru import logger
from app.config import get_settings
from app.model.proxy_log_model import ProxyLogModel, ProxyLogStatus
from app.repository.proxy_log_repo import ProxyLogRepository
from app.repository.system_config_repo import SystemConfigRepository
from libs.meta_ads_collector import (
    FilterConfig,
    MetaAdsCollector,
    ProxyError,
    ProxyPool,
    RateLimitError,
    SessionExpiredError,
)
from libs.meta_ads_crawler import MetaAdsCrawler
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from app.redis import LOCK_PROXY_KEY_VALUE, get_client as get_redis

from app.service.proxy_scrape import ProxyScrapeService
from app.service.proxy_rotation import ProxyRotationService
from app.db import close_client, proxy_logs_sync_collection, system_configs_collection

OnAdCallback = Callable[[dict[str, Any], str, int], None]

PROXY_LOCK_PREFIX = "proxy:lock:"
EMPTY_PROXY_LOCK_PREFIX = "proxy:lock:empty:"
EMPTY_PROXY_SLOTS = 3
HEALTH_CHECK_URL = "https://spy.dropshiptools.xyz/api/health"

RETRY_INTERVAL = 10

META_PAGE_SIZE = 30


_executor = ThreadPoolExecutor(max_workers=4)


class ReachThresholdReached(Exception):
    """Raised from on_ad when mustReach / storeMustReach is satisfied."""


class MetaCollectorService:
    def __init__(
        self,
        proxy_pool: ProxyPool | None = None,
        proxy_rotation_service: ProxyRotationService | None = None,
        *,
        proxy_scrape: ProxyScrapeService | None = None,
        fallback_proxies: list[str] | None = None,
        proxy_log_repo: ProxyLogRepository | None = None,
    ) -> None:
        self._proxy_pool = proxy_pool
        self._proxy_scrape = proxy_scrape
        self._fallback_proxies = list(fallback_proxies or [])
        self._settings = get_settings()
        if not proxy_rotation_service:
            proxy_rotation_service = ProxyRotationService()

        self._proxy_rotation_service = proxy_rotation_service
        self._proxy_log_repo = proxy_log_repo or ProxyLogRepository(
            proxy_logs_sync_collection(self._settings)
        )

    def search(
        self,
        *,
        keyword: str,
        start_date: date,
        end_date: date,
        country: str,
        status_filter: str | None = None,
        media_type: str | None = None,
        max_results: int | None = None,
        on_ad: OnAdCallback | None = None,
    ) -> int:
        logger.info(
            "Searching Meta ads keyword={!r} country={} start={} end={} "
            "max_results={} proxy_source={}",
            keyword,
            country,
            start_date,
            end_date,
            max_results,
            self._proxy_source_label(),
        )
        return self._search_with_retry(
            keyword=keyword,
            country=country,
            max_results=max_results,
            on_ad=on_ad,
            status_filter=status_filter,
            start_date=str(start_date),
            end_date=str(end_date),
        )

    def _proxy_source_label(self) -> str:
        if self._proxy_scrape is not None and self._proxy_scrape.enabled:
            return "proxyscrape-random"
        if self._fallback_proxies:
            return "fallback-random"
        if self._proxy_pool is not None and len(self._proxy_pool) > 0:
            return "mongo-random"
        return "none"

    def _proxy_candidates(self) -> list[str]:
        if self._proxy_scrape is not None and self._proxy_scrape.enabled:
            scraped = self._proxy_scrape.get_proxies()
            if scraped:
                return scraped

        if self._fallback_proxies:
            return list(self._fallback_proxies)

        if self._proxy_pool is not None and len(self._proxy_pool) > 0:
            return list(self._proxy_pool.alive_proxies)

        return []

    def _random_proxy_for_search(self) -> ProxyPool | str | None:
        candidates = self._proxy_candidates()
        if not candidates:
            return None

        shuffled = list(candidates)
        random.shuffle(shuffled)
        try:
            pool = ProxyPool(shuffled)
        except ProxyError as exc:
            logger.warning("Failed to build random ProxyPool: {}", exc)
            return shuffled[0] if shuffled else None

        logger.info(
            "Random proxy rotation: first={!r} total={}",
            shuffled[0],
            len(shuffled),
        )
        return pool

    @staticmethod
    def _raise_if_failed(futures: list[Future[None]]) -> None:
        """Re-raise the first callback error (incl. ReachThresholdReached)."""
        pending: list[Future[None]] = []
        first_error: BaseException | None = None
        for future in futures:
            if not future.done():
                pending.append(future)
                continue
            exc = future.exception()
            if exc is not None and first_error is None:
                first_error = exc
        futures[:] = pending
        if first_error is not None:
            raise first_error

    @staticmethod
    def _wait_callbacks(futures: list[Future[None]]) -> None:
        """Wait for in-flight on_ad work; prefer ReachThresholdReached."""
        threshold: ReachThresholdReached | None = None
        other: BaseException | None = None
        for future in futures:
            try:
                future.result()
            except ReachThresholdReached as exc:
                if threshold is None:
                    threshold = exc
            except BaseException as exc:  # noqa: BLE001 — surface to caller
                if other is None:
                    other = exc
        futures.clear()
        if threshold is not None:
            raise threshold
        if other is not None:
            raise other

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        retry=retry_if_exception_type(
            (
                OSError,
                TimeoutError,
                ConnectionError,
                RateLimitError,
                ProxyError,
                SessionExpiredError,
            )
        ),
    )
    def _search_with_retry(
        self,
        *,
        keyword: str,
        country: str,
        status_filter: str | None,
        max_results: int | None,
        on_ad: OnAdCallback | None,
        start_date: str | None,
        end_date: str | None,
    ) -> int:
        count = 0
        futures: list[Future[None]] = []

        def handle_captured_ad(ad: dict[str, Any], link_ads_url: str) -> None:
            """Dispatch each ad while its GraphQL response is being captured."""
            nonlocal count
            if on_ad is None:
                return
            if max_results is not None and count >= max_results:
                return
            if futures:
                self._raise_if_failed(futures)
            # The crawler returns this list in descending total-impressions order.
            rank = count + 1
            futures.append(_executor.submit(on_ad, ad, link_ads_url, rank))
            count += 1

        def handle_request_log(
            *,
            request_id: str = None,
            body=None,
            headers=None,
            status_code=None,
            status=None,
            err=None,
        ) -> None:
            log = self._proxy_log_repo.update_one(
                request_id=request_id,
                body=body,
                headers=headers,
                status_code=status_code,
                status=status,
                err=err,
            )
            if (
                isinstance(log, dict)
                and isinstance(err, dict)
                and err.get("err") == "Rate limit"
            ):
                if log.get("proxy"):
                    client = get_redis()
                    proxy = log["proxy"]
                    key = f"proxy:lock:{proxy}"
                    client.set(key, LOCK_PROXY_KEY_VALUE, ex=60 * 5)

        try:
            crawler = MetaAdsCrawler()
            ads = crawler.search(
                query=keyword,
                country=country,
                status=status_filter,
                max_results=max_results,
                start_date=start_date,
                end_date=end_date,
                proxy=self._settings.proxy_manager,
                on_ad=handle_captured_ad if on_ad is not None else None,
                on_request_log=handle_request_log,
            )
            if on_ad is None:
                count = len(ads)
            self._wait_callbacks(futures)
        except ReachThresholdReached as exc:
            try:
                self._wait_callbacks(futures)
            except ReachThresholdReached:
                pass
            logger.info(
                "Stopped Meta search early for keyword={!r}: {}",
                keyword,
                exc,
            )
        except BaseException:
            try:
                self._wait_callbacks(futures)
            except BaseException:
                pass
            raise
        logger.info("Collected {} ads for keyword={!r}", count, keyword)
        return count
