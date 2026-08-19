import base64
from email import message
import json
import re
import time
from collections.abc import Callable
from datetime import date
from typing import Any
from urllib.parse import parse_qsl, urlencode

from selenium import webdriver
from selenium.common.exceptions import WebDriverException

from libs.meta_ads_collector.exceptions import RateLimitError
from libs.meta_ads_collector.proxy_pool import ProxyPool
from .meta_ads_embedded_json import getAds
import random
from .client import MetaAdsClient
from .meta_ads_embedded_json import normalize_ad

OnAdCallback = Callable[[dict[str, Any], str], None]
OnRequestLogCallback = Callable[..., None]
AD_LIBRARY_URL = "https://www.facebook.com/ads/library/"
CHROME_VERSIONS: list[tuple[str, str]] = [
    ("140", "140.0.7200.110"),
    ("141", "141.0.7300.120"),
    ("142", "142.0.7400.100"),
    ("143", "143.0.7499.169"),
    ("144", "144.0.7559.109"),
    ("145", "145.0.7632.110"),
]
PLATFORMS: list[dict[str, str]] = [
    {
        "name": "windows",
        "ua_os": "Windows NT 10.0; Win64; x64",
        "sec_ch_ua_platform": '"Windows"',
        "platform_version": '"15.0.0"',
    },
    {
        "name": "macos",
        "ua_os": "Macintosh; Intel Mac OS X 10_15_7",
        "sec_ch_ua_platform": '"macOS"',
        "platform_version": '"14.5.0"',
    },
    {
        "name": "macos",
        "ua_os": "Macintosh; Intel Mac OS X 10_15_7",
        "sec_ch_ua_platform": '"macOS"',
        "platform_version": '"13.6.0"',
    },
    {
        "name": "windows",
        "ua_os": "Windows NT 10.0; Win64; x64",
        "sec_ch_ua_platform": '"Windows"',
        "platform_version": '"10.0.0"',
    },
]
VIEWPORTS: list[tuple[int, int]] = [
    (1366, 768),
    (1440, 900),
    (1536, 864),
    (1920, 1080),
    (2560, 1440),
    (1680, 1050),
    (1280, 720),
    (1600, 900),
]

DPR_VALUES: list[float] = [1, 1.25, 1.5, 2, 3]

# ---------------------------------------------------------------------------
# "Not A Brand" hint variations
# ---------------------------------------------------------------------------
NOT_A_BRAND_HINTS: list[tuple[str, str]] = [
    ("Not_A Brand", "24"),
    ("Not/A)Brand", "8"),
    ("Not.A/Brand", "8"),
    ("Not A(Brand", "99"),
]


def create_driver(
    proxy: str | None = None,
) -> webdriver.Chrome:
    options = webdriver.ChromeOptions()
    # if proxy:
    #     options.add_argument(f"--proxy-server=http://{proxy}")
    chrome_major, chrome_full = random.choice(CHROME_VERSIONS)

    # Pick random platform
    platform = random.choice(PLATFORMS)

    # Pick viewport and DPR
    viewport_width, viewport_height = random.choice(VIEWPORTS)
    dpr = random.choice(DPR_VALUES)

    # Pick a "Not a Brand" variant
    nab_name, nab_version = random.choice(NOT_A_BRAND_HINTS)

    # Build User-Agent
    user_agent = (
        f"Mozilla/5.0 ({platform['ua_os']}) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{chrome_major}.0.0.0 Safari/537.36"
    )
    options.add_argument("--force-webrtc-ip-handling-policy=disable_non_proxied_udp")
    options.add_argument("--disable-quic")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument(f"--user-agent={user_agent}")
    # options.add_argument("--headless=false")
    options.add_argument("--no-sandbox")
    options.add_argument("--window-size=1440,1200")
    options.add_argument("--lang=en-US")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-dev-shm-usage")
    return webdriver.Chrome(options=options)


def decode_for_filter(body: bytes) -> str:
    return body.decode("utf-8", errors="replace")


def raise_for_graphql_error(response_body: str) -> None:
    """Turn GraphQL errors returned with HTTP 200 into actionable errors."""
    payload = response_body.removeprefix("for (;;);").lstrip()
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return

    errors = data.get("errors", []) if isinstance(data, dict) else []
    for error in errors:
        if not isinstance(error, dict):
            continue
        code = error.get("code")
        message = str(error.get("message", "GraphQL request failed"))
        if code == 1675004 or "rate limit" in message.lower():
            print(f"GraphQL rate limit error: {message}")
            raise RateLimitError(message, retry_after=30)


class MetaAdsCrawler:
    """Crawl ads displayed by the Meta Ad Library in an existing browser session."""

    AD_TYPE_ALL = "all"
    AD_TYPE_POLITICAL = "political_and_issue_ads"
    AD_TYPE_HOUSING = "housing"
    AD_TYPE_EMPLOYMENT = "employment"
    AD_TYPE_CREDIT = "credit"

    STATUS_ACTIVE = "active"
    STATUS_INACTIVE = "inactive"
    STATUS_ALL = "all"

    _AD_TYPES = {
        AD_TYPE_ALL,
        AD_TYPE_POLITICAL,
        AD_TYPE_HOUSING,
        AD_TYPE_EMPLOYMENT,
        AD_TYPE_CREDIT,
    }
    _STATUSES = {STATUS_ACTIVE, STATUS_INACTIVE, STATUS_ALL}

    def __init__(self) -> None:
        print("Initializing MetaAdsCrawler...")

    def search(
        self,
        query: str = "",
        country: str = "US",
        ad_type: str = AD_TYPE_ALL,
        status: str = STATUS_ACTIVE,
        max_results: int | None = None,
        on_ad: OnAdCallback | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
        filter_config: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        capture_duration_seconds: float | None = 180,
        proxy: str = None,
        on_request_log: OnRequestLogCallback | None = None,
    ) -> list[dict[str, Any]]:
        """Capture Meta Ad Library responses and return normalized advertisements.

        ``on_ad`` is invoked as soon as each new ad is parsed from an
        embedded payload or captured GraphQL response.

        ``filter_config`` accepts additional URL query parameters, for example
        ``"media_type=video&search_type=keyword_exact_phrase"``.
        Dates must use the ISO ``YYYY-MM-DD`` format when supplied.
        """
        normalized_ad_type = ad_type.lower()
        normalized_status = status.lower()
        if normalized_ad_type not in self._AD_TYPES:
            raise ValueError(f"Unsupported ad_type: {ad_type}")
        if normalized_status not in self._STATUSES:
            raise ValueError(f"Unsupported status: {status}")
        if max_results is not None and max_results < 1:
            raise ValueError("max_results must be greater than zero")
        if capture_duration_seconds is not None and capture_duration_seconds <= 0:
            raise ValueError("capture_duration_seconds must be greater than zero")

        self._validate_date(start_date, "start_date")
        self._validate_date(end_date, "end_date")
        self.capture_responses(
            query=query,
            country=country,
            ad_type=normalized_ad_type,
            status=normalized_status,
            filter_config=filter_config,
            start_date=start_date,
            end_date=end_date,
            proxy=proxy,
            on_ad=on_ad,
        )

        ads_by_id: dict[str, dict[str, Any]] = {}
        ads = list(ads_by_id.values())
        if max_results is not None:
            ads = ads[:max_results]
        if progress_callback is not None:
            progress_callback(len(ads), max_results or len(ads))
        return ads

    @staticmethod
    def _validate_date(value: str | None, parameter_name: str) -> None:
        if value is None:
            return
        try:
            date.fromisoformat(value)
        except ValueError as error:
            raise ValueError(
                f"{parameter_name} must use YYYY-MM-DD format, got {value!r}"
            ) from error

    def _build_search_url(
        self,
        query: str,
        country: str,
        ad_type: str,
        status: str,
        filter_config: str | None,
        start_date: str | None,
        end_date: str | None,
    ) -> str:
        parameters = {
            "active_status": status,
            "ad_type": ad_type,
            "country": country.upper(),
            "q": query,
            "search_type": "keyword_unordered",
            "media_type": "all",
            "sort_data[direction]": "desc",
            "sort_data[mode]": "total_impressions",
        }
        if start_date is not None:
            parameters["start_date[min]"] = start_date
        if end_date is not None:
            parameters["start_date[max]"] = end_date
        if filter_config:
            parameters.update(dict(parse_qsl(filter_config, keep_blank_values=True)))
        return AD_LIBRARY_URL + "?" + urlencode(parameters)

    def capture_responses(
        self,
        query: str,
        country: str,
        ad_type: str,
        status: str,
        filter_config: str | None,
        start_date: str | None,
        end_date: str | None,
        proxy: str = None,
        on_ad: OnAdCallback | None = None,
    ) -> list[str | dict[str, Any]]:
        """Capture network responses that match the supplied filters."""

        active_driver: webdriver.Chrome | None = None
        captured_response_bodies: list[str | dict[str, Any]] = []
        emitted_ad_ids: set[str] = set()
        callback_raised = False

        def emit_ads(
            response_body: str | dict[str, Any],
        ) -> None:
            """Invoke the callback once for each newly observed normalized ad."""
            nonlocal callback_raised
            if on_ad is None:
                return
            ads = [response_body]
            for ad in ads:
                ad_id = ad.get("ad_archive_id")
                if ad_id is None:
                    continue
                normalized_id = str(ad_id)
                if normalized_id not in emitted_ad_ids:
                    emitted_ad_ids.add(normalized_id)
                    try:
                        on_ad(ad, page_url)
                    except Exception:
                        callback_raised = True
                        raise

        page_url = self._build_search_url(
            query=query,
            country=country,
            ad_type=ad_type,
            status=status,
            filter_config=filter_config,
            start_date=start_date,
            end_date=end_date,
        )
        active_driver = None
        try:
            active_driver = create_driver(proxy=proxy)
            print(f"Open Page: {page_url}")
            active_driver.get(page_url)
            response = getAds(active_driver)
            ads = response.ads
            cursor = response.cursor
            for ad in ads:
                emit_ads(ad)

            html = active_driver.page_source
            active_driver.quit()

            client = MetaAdsClient(proxy=proxy, html=html)

            while cursor is not None:
                response, cursor = client.search_ads(
                    cursor=cursor,
                    query=query,
                    active_status=status,
                    ad_type=ad_type,
                    start_date=start_date,
                    end_date=end_date,
                    sort_mode="SORT_BY_TOTAL_IMPRESSIONS",
                )
                print("response", response)
                if response and hasattr(response, "ads"):
                    ads.extend(response.ads)
                    for ad in response.ads:
                        normalize_ad(ad)
                        emit_ads(ad)
                    print(f"Captured {len(response.ads)} ads. Total: {len(ads)}")

            print(f"Total captured {len(ads)} ads.")
            return ads
        except Exception:
            raise
        finally:
            if active_driver is not None:
                active_driver.quit()
