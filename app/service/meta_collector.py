from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time
from typing import Any

from loguru import logger
from meta_ads_collector import (
    FilterConfig,
    MetaAdsCollector,
    ProxyError,
    RateLimitError,
    SessionExpiredError,
)
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

OnAdCallback = Callable[[dict[str, Any]], None]


class MetaCollectorService:
    def search(
        self,
        *,
        keyword: str,
        start_date: date,
        end_date: date,
        country: str,
        max_results: int | None = None,
        on_ad: OnAdCallback | None = None,
    ) -> int:
        filter_config = FilterConfig(
            start_date=datetime.combine(start_date, time.min),
            end_date=datetime.combine(end_date, time.max),
        )
        logger.info(
            "Searching Meta ads keyword={!r} country={} start={} end={} max_results={}",
            keyword,
            country,
            start_date,
            end_date,
            max_results,
        )
        return self._search_with_retry(
            keyword=keyword,
            country=country,
            max_results=max_results,
            filter_config=filter_config,
            on_ad=on_ad,
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
        max_results: int | None,
        filter_config: FilterConfig,
        on_ad: OnAdCallback | None,
    ) -> int:
        count = 0
        with MetaAdsCollector(timeout=80) as collector:
            for ad in collector.search(
                query=keyword,
                country=country,
                max_results=max_results,
                filter_config=filter_config,
            ):
                raw = ad.to_dict()
                if on_ad is not None:
                    on_ad(raw)
                count += 1
        logger.info("Collected {} ads for keyword={!r}", count, keyword)
        return count
