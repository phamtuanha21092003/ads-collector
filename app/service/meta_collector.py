from __future__ import annotations

import random
from collections.abc import Callable
from datetime import date, datetime, time
from typing import Any
from concurrent.futures import Future, ThreadPoolExecutor

from loguru import logger
from libs.meta_ads_collector import (
    FilterConfig,
    MetaAdsCollector,
    ProxyError,
    ProxyPool,
    RateLimitError,
    SessionExpiredError,
)
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.service.proxy_scrape import ProxyScrapeService

OnAdCallback = Callable[[dict[str, Any]], None]

META_PAGE_SIZE = 1000


_executor = ThreadPoolExecutor(max_workers=4)


class ReachThresholdReached(Exception):
    """Raised from on_ad when mustReach / storeMustReach is satisfied."""


class MetaCollectorService:
    def __init__(
        self,
        proxy_pool: ProxyPool | None = None,
        *,
        proxy_scrape: ProxyScrapeService | None = None,
        fallback_proxies: list[str] | None = None,
    ) -> None:
        self._proxy_pool = proxy_pool
        self._proxy_scrape = proxy_scrape
        self._fallback_proxies = list(fallback_proxies or [])

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
        filter_config = FilterConfig(
            start_date=datetime.combine(start_date, time.min),
            end_date=datetime.combine(end_date, time.max),
            media_type=media_type,
        )
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
            filter_config=filter_config,
            on_ad=on_ad,
            status_filter=status_filter,
            start_date=start_date,
            end_date=end_date
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
        filter_config: FilterConfig,
        on_ad: OnAdCallback | None,
        start_date: str | None,
        end_date: str | None,
    ) -> int:
        active_proxy = self._random_proxy_for_search()
        count = 0
        futures: list[Future[None]] = []
        with MetaAdsCollector(timeout=180, proxy=active_proxy, rate_limit_delay=3) as collector:
            try:
                for ad in collector.search(
                    status=status_filter or "ALL",
                    query=keyword,
                    country=country,
                    max_results=max_results,
                    filter_config=filter_config,
                    page_size=META_PAGE_SIZE,
                ):
                    if futures:
                        self._raise_if_failed(futures)
                    raw = ad.to_dict()
                    if on_ad is not None:
                        futures.append(_executor.submit(on_ad, raw))
                    count += 1
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
