from __future__ import annotations

import time
from typing import Any
from urllib.parse import unquote, urlparse

import requests
from loguru import logger


def normalize_proxy_to_host_port_user_pass(raw: str) -> str | None:
    value = (raw or "").strip()
    if not value or value.startswith("#"):
        return None

    parts = value.split(":")
    if "@" not in value and "://" not in value and len(parts) == 4:
        host, port, user, password = parts
        if host and port and user and password:
            return f"{host}:{port}:{user}:{password}"

    if "@" not in value and "://" not in value and len(parts) == 2:
        host, port = parts
        if host and port:
            return f"{host}:{port}"

    candidate = value if "://" in value else f"http://{value}"
    parsed = urlparse(candidate)
    host = parsed.hostname
    port = parsed.port
    user = unquote(parsed.username or "")
    password = unquote(parsed.password or "")
    if not host or port is None:
        return None
    if user and password:
        return f"{host}:{port}:{user}:{password}"
    return f"{host}:{port}"


class ProxyScrapeService:
    def __init__(
        self,
        *,
        url: str,
        api_token: str,
        cache_ttl_seconds: float = 300.0,
        timeout: float = 30.0,
    ) -> None:
        self._url = (url or "").strip()
        self._api_token = (api_token or "").strip()
        self._cache_ttl = max(30.0, float(cache_ttl_seconds))
        self._timeout = timeout
        self._cached: list[str] = []
        self._cached_at: float = 0.0

    @property
    def enabled(self) -> bool:
        return bool(self._url and self._api_token)

    def get_proxies(self, *, force_refresh: bool = False) -> list[str]:
        if not self.enabled:
            return []

        now = time.time()
        if (
            not force_refresh
            and self._cached
            and (now - self._cached_at) < self._cache_ttl
        ):
            return list(self._cached)

        proxies = self._fetch()
        if proxies:
            self._cached = proxies
            self._cached_at = now
            logger.info("ProxyScrape loaded {} proxies", len(proxies))
        elif self._cached:
            logger.warning(
                "ProxyScrape refresh failed/empty — reusing {} cached proxies",
                len(self._cached),
            )
            return list(self._cached)
        else:
            logger.warning("ProxyScrape returned no proxies")
        return list(self._cached)

    def _fetch(self) -> list[str]:
        params: dict[str, Any] = {
            "proxy_format": "ipport",
            "credential_format": "2",
            "protocol": "http",
            "type": "getproxies",
            "format": "credentials",
        }
        headers = {"api-token": self._api_token}
        try:
            response = requests.get(
                self._url,
                params=params,
                headers=headers,
                timeout=self._timeout,
            )
            response.raise_for_status()
        except Exception as exc:
            logger.exception("ProxyScrape request failed: {}", exc)
            return []

        proxies: list[str] = []
        for line in response.text.strip().splitlines():
            normalized = normalize_proxy_to_host_port_user_pass(line)
            if normalized:
                proxies.append(normalized)
        return proxies
