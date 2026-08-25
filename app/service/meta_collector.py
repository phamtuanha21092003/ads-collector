from __future__ import annotations

import random
from redis import Redis
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
from app.repository.proxy_repo import ProxyRepository
from app.repository.system_config_repo import SystemConfigRepository

from libs.meta_ads_crawler import MetaAdsCrawler
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from app.redis import LOCK_PROXY_KEY_VALUE, get_client as get_redis

from app.service.proxy_scrape import (
    ProxyScrapeService,
    normalize_proxy_to_host_port_user_pass,
)
from app.db import (
    close_client,
    proxies_sync_collection,
    proxy_logs_sync_collection,
    system_configs_collection,
)
from app.util.telegram_util import send_telegram_message
from app.exceptions import (
    ReachThresholdReached,
    RateLimitError,
    SessionExpiredError,
    ProxyError,
)

OnAdCallback = Callable[[dict[str, Any], str, int], None]

PROXY_LOCK_PREFIX = "proxy:lock:"
EMPTY_PROXY_LOCK_PREFIX = "proxy:lock:empty:"
EMPTY_PROXY_SLOTS = 3
HEALTH_CHECK_URL = "https://spy.dropshiptools.xyz/api/health"

RETRY_INTERVAL = 10

META_PAGE_SIZE = 30


_executor = ThreadPoolExecutor(max_workers=4)


class MetaCollectorService:
    def __init__(
        self,
        *,
        proxy_scrape: ProxyScrapeService | None = None,
        fallback_proxies: list[str] | None = None,
        proxy_log_repo: ProxyLogRepository | None = None,
    ) -> None:
        self._proxy_scrape = proxy_scrape
        self._fallback_proxies = list(fallback_proxies or [])
        self._settings = get_settings()
        self._proxy_log_repo = proxy_log_repo or ProxyLogRepository(
            proxy_logs_sync_collection(self._settings)
        )
        self._proxy_repo = ProxyRepository(
            proxies_sync_collection(self._settings)
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

    @staticmethod
    def _standard_proxy_format(raw: str) -> str:
        """Normalize to host:port or host:port:user:pass (Redis lock key)."""
        return normalize_proxy_to_host_port_user_pass(raw) or raw.strip()

    def _locked_proxy_counts(self, client: Redis) -> tuple[int, int]:
        locked = 0
        cursor = 0
        while True:
            cursor, keys = client.scan(cursor, match=f"{PROXY_LOCK_PREFIX}*", count=200)
            locked += sum(
                1
                for key in keys
                if not str(key).startswith(EMPTY_PROXY_LOCK_PREFIX)
                and client.get(key) == LOCK_PROXY_KEY_VALUE
            )
            if cursor == 0:
                break
        total = proxies_sync_collection(self._settings).count_documents({})
        return locked, int(total)

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
            count += 1
            # The crawler returns this list in descending total-impressions order.
            rank = count
            futures.append(_executor.submit(on_ad, ad, link_ads_url, rank))

        def handle_request_log(
            *,
            request_id: str = None,
            body=None,
            headers=None,
            status_code=None,
            status=None,
            err=None,
            request_body=None,
        ) -> None:
            log = self._proxy_log_repo.update_one(
                request_id=request_id,
                body=body,
                headers=headers,
                status_code=status_code,
                status=status,
                err=err,
                request_body=request_body,
            )
            if (
                isinstance(log, dict)
                and isinstance(err, dict)
                and err.get("err") == "Rate limit"
            ):
                raw_proxy = log.get("proxy")
                if not raw_proxy:
                    return
                
                limit_time = 30
                proxy = self._standard_proxy_format(str(raw_proxy))
                client = get_redis()
                key = f"{PROXY_LOCK_PREFIX}{raw_proxy}"
                value = client.get(key)

                if value == LOCK_PROXY_KEY_VALUE:
                    return

                p = self._proxy_repo.find_proxy_by_domain_and_update(raw_proxy)
                if isinstance(p, dict):
                    def calculate_limit(n: int) -> int:
                        if n == 0:
                            return 10

                        return min(10 + 30 * n, 720)

                    limit_time = calculate_limit(p.get('timeRatelimit', 0))
                client.set(key, LOCK_PROXY_KEY_VALUE, ex=60 * limit_time)

                locked, total = self._locked_proxy_counts(client)
                print(f"proxy={proxy} locked={raw_proxy}/{total}")
                logger.info(
                    "Rate-limit proxy standard={} raw={} locked={}/{}",
                    proxy,
                    raw_proxy,
                    locked,
                    total,
                )

                send_telegram_message(
                    f"Proxy {proxy} is locked for the next {limit_time} minutes ({locked}/{total})"
                )

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
