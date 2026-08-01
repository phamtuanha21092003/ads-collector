from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time
from typing import Any

from loguru import logger
from meta_ads_collector import (
    FilterConfig,
    MetaAdsCollector,
    ProxyError,
    ProxyPool,
    RateLimitError,
    SessionExpiredError,
)
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

OnAdCallback = Callable[[dict[str, Any]], None]


class ReachThresholdReached(Exception):
    """Raised from on_ad when mustReach / storeMustReach is satisfied."""


class MetaCollectorService:
    def __init__(self, proxy_pool: ProxyPool | None = None) -> None:
        self._proxy_pool = proxy_pool

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
            media_type=media_type
        )
        logger.info(
            "Searching Meta ads keyword={!r} country={} start={} end={} "
            "max_results={} proxy={}",
            keyword,
            country,
            start_date,
            end_date,
            max_results,
            "rotation" if self._proxy_pool is not None else "none",
        )
        return self._search_with_retry(
            keyword=keyword,
            country=country,
            max_results=max_results,
            filter_config=filter_config,
            on_ad=on_ad,
            status_filter=status_filter,
        )

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
    ) -> int:
        count = 0
        with MetaAdsCollector(timeout=180, proxy=self._proxy_pool) as collector:
            try:
                for ad in collector.search(
                    status=status_filter or "ALL",
                    query=keyword,
                    country=country,
                    max_results=max_results,
                    filter_config=filter_config,
                    page_size=30,
                ):
                    raw = ad.to_dict()
                    if on_ad is not None:
                        on_ad(raw)
                    count += 1
            except ReachThresholdReached as exc:
                logger.info(
                    "Stopped Meta search early for keyword={!r}: {}",
                    keyword,
                    exc,
                )
        logger.info("Collected {} ads for keyword={!r}", count, keyword)
        return count
