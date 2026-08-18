from __future__ import annotations

import json
from typing import Any

from loguru import logger
from libs.meta_ads_collector import ProxyPool

from app.model.system_config_model import PROXIES_ROTATION_META_KEY
from app.repository.system_config_repo import SystemConfigRepository


def parse_proxy_list(raw: str) -> list[str]:
    """Parse system_configs value into proxy strings.

    Supports:
    - JSON array: ``["host:port:user:pass", ...]``
    - Newline / comma separated text (``#`` comments and blanks skipped)
    """
    text = (raw or "").strip()
    if not text:
        return []

    if text.startswith("["):
        try:
            data: Any = json.loads(text)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, list):
            return [str(item).strip() for item in data if str(item).strip()]

    proxies: list[str] = []
    for part in text.replace(",", "\n").splitlines():
        line = part.strip()
        if not line or line.startswith("#"):
            continue
        proxies.append(line)
    return proxies


class ProxyRotationService:
    def __init__(self) -> None:
        self._pool: ProxyPool | None = None
        self._proxies: list[str] = []

    @property
    def pool(self) -> ProxyPool | None:
        return self._pool

    async def load_from_mongo(self, repo: SystemConfigRepository) -> None:
        config = await repo.get_by_key(PROXIES_ROTATION_META_KEY)
        if config is None or not (config.value or "").strip():
            self._pool = None
            logger.info(
                "No system config {!r} — Meta collector runs without proxy",
                PROXIES_ROTATION_META_KEY,
            )
            return

        proxies = parse_proxy_list(config.value)
        if not proxies:
            self._pool = None
            logger.warning(
                "System config {!r} is empty after parse — running without proxy",
                PROXIES_ROTATION_META_KEY,
            )
            return

        self._proxies = proxies

        try:
            self._pool = ProxyPool(proxies)
        except Exception as exc:
            self._pool = None
            logger.exception(
                "Failed to init ProxyPool from {!r}: {} — running without proxy",
                PROXIES_ROTATION_META_KEY,
                exc,
            )
            return

        logger.info(
            "Loaded {} Meta proxies from system config {!r}",
            len(self._pool),
            PROXIES_ROTATION_META_KEY,
        )
