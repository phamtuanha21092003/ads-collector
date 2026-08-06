import base64
import json
import re
import time
from collections.abc import Callable
from datetime import date
from typing import Any
from urllib.parse import parse_qsl, urlencode

from selenium import webdriver
from selenium.common.exceptions import WebDriverException

from libs.meta_ads_collector.proxy_pool import ProxyPool
from .meta_ads_embedded_json import extract_ads_from_scripts, getAds

AD_LIBRARY_URL = "https://www.facebook.com/ads/library/"


def create_driver(
    headless: bool,
    proxy: str | None = None,
) -> webdriver.Chrome:
    options = webdriver.ChromeOptions()
    if proxy:
        options.add_argument(f"--proxy-server={proxy}")
    if headless:
        print("Running Chrome in headless mode.")
    options.add_argument("--headless=false")
    options.add_argument("--no-sandbox")
    options.add_argument("--window-size=1440,1200")
    options.add_argument("--lang=en-US")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-dev-shm-usage")

    # Cho phép đọc sự kiện Network.* qua performance log.
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    return webdriver.Chrome(options=options)


def read_response_body(
    driver: webdriver.Chrome,
    request_id: str,
    retries: int = 4,
) -> tuple[bytes, bool]:
    """Đọc body sớm để tránh requestId hết hiệu lực."""
    last_error: Exception | None = None

    for attempt in range(retries):
        try:
            result = driver.execute_cdp_cmd(
                "Network.getResponseBody",
                {"requestId": request_id},
            )
            body_text = result.get("body", "")
            is_base64 = bool(result.get("base64Encoded"))

            if is_base64:
                return base64.b64decode(body_text), True
            return body_text.encode("utf-8"), False
        except WebDriverException as error:
            last_error = error
            time.sleep(0.15 * (attempt + 1))

    raise RuntimeError(f"Không đọc được response body: {last_error}")


def decode_for_filter(body: bytes) -> str:
    return body.decode("utf-8", errors="replace")


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
        on_ad: Callable[[dict[str, Any]], None] | None = None,
        stop_check: Callable[[], None] | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
        filter_config: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        capture_duration_seconds: float | None = 60,
        auto_scroll: bool = True,
        scroll_interval_seconds: float = 3.0,
        proxy: str | ProxyPool | None = None,
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
        response_bodies = capture_responses(
            page_url=self._build_search_url(
                query=query,
                country=country,
                ad_type=normalized_ad_type,
                status=normalized_status,
                filter_config=filter_config,
                start_date=start_date,
                end_date=end_date,
            ),
            mime_filter="",
            request_body_filter="AdLibrarySearchPaginationQuery",
            response_body_filter="",
            duration_seconds=capture_duration_seconds,
            headless=False,
            auto_scroll=auto_scroll,
            scroll_interval_seconds=scroll_interval_seconds,
            proxy=proxy,
            on_ad=on_ad,
            stop_check=stop_check,
        )

        ads_by_id: dict[str, dict[str, Any]] = {}
        for response_body in response_bodies:
            if isinstance(response_body, dict):
                extracted_ads = [response_body]
            else:
                extracted_ads = extract_ads_from_scripts([response_body])

            for ad in extracted_ads:
                ad_id = ad.get("ad_archive_id")
                if ad_id is not None:
                    ads_by_id[str(ad_id)] = ad

            if progress_callback is not None:
                progress_callback(len(ads_by_id), max_results or len(ads_by_id))

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

    @staticmethod
    def _build_search_url(
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
        }
        if start_date is not None:
            parameters["start_date[min]"]= start_date
        if end_date is not None:
            parameters["start_date[max]"] = end_date
        if filter_config:
            parameters.update(dict(parse_qsl(filter_config, keep_blank_values=True)))
        return AD_LIBRARY_URL + "?" + urlencode(parameters)


def capture_responses(
    page_url: str,
    mime_filter: str,
    request_body_filter: str,
    response_body_filter: str,
    duration_seconds: float | None,
    headless: bool,
    auto_scroll: bool,
    scroll_interval_seconds: float,
    proxy: str | ProxyPool | None = None,
    on_ad: Callable[[dict[str, Any]], None] | None = None,
    stop_check: Callable[[], None] | None = None,
) -> list[str | dict[str, Any]]:
    """Capture network responses that match the supplied filters."""
    url_filter = "/api/graphql/"
    proxy_pool = proxy if isinstance(proxy, ProxyPool) else None
    active_proxy = proxy_pool.get_next() if proxy_pool else proxy

    active_driver: webdriver.Chrome | None = None

    request_details: dict[str, dict[str, Any]] = {}
    captured_response_bodies: list[str | dict[str, Any]] = []
    seen_response_ids: set[str] = set()
    emitted_ad_ids: set[str] = set()
    callback_raised = False
    stop_check_raised = False

    def emit_ads(response_body: str | dict[str, Any]) -> None:
        """Invoke the callback once for each newly observed normalized ad."""
        nonlocal callback_raised
        if on_ad is None:
            return
        ads = (
            [response_body]
            if isinstance(response_body, dict)
            else extract_ads_from_scripts([response_body])
        )
        for ad in ads:
            ad_id = ad.get("ad_archive_id")
            if ad_id is None:
                continue
            normalized_id = str(ad_id)
            if normalized_id not in emitted_ad_ids:
                emitted_ad_ids.add(normalized_id)
                try:
                    on_ad(ad)
                except Exception:
                    callback_raised = True
                    raise

    try:
        active_driver = create_driver(proxy=active_proxy, headless=headless)
        active_driver.execute_cdp_cmd(
            "Network.enable",
            {
                "maxTotalBufferSize": 100_000_000,
                "maxResourceBufferSize": 50_000_000,
                "maxPostDataSize": 10_000_000,
            },
        )

        print(f"Open Page: {page_url}")
        active_driver.get(page_url)
        embedded_ads = getAds(active_driver)
        captured_response_bodies.extend(embedded_ads)
        for ad in embedded_ads:
            emit_ads(ad)

        if duration_seconds is None:
            print("Scroll until on_ad stops the crawl.")
        else:
            print(f"Duration_seconds {duration_seconds:g} seconds. ")

        started_at = time.monotonic()
        next_scroll_at = started_at + scroll_interval_seconds

        while (
            duration_seconds is None
            or time.monotonic() - started_at < duration_seconds
        ):
            if stop_check is not None:
                try:
                    stop_check()
                except Exception:
                    stop_check_raised = True
                    raise
            now = time.monotonic()

            if auto_scroll and now >= next_scroll_at:
                active_driver.execute_script(
                    "window.scrollTo(0, document.documentElement.scrollHeight);"
                )
                next_scroll_at = now + scroll_interval_seconds

            entries = active_driver.get_log("performance")

            for entry in entries:
                try:
                    event = json.loads(entry["message"])["message"]
                    method = event.get("method")
                    params = event.get("params", {})
                except (KeyError, TypeError, json.JSONDecodeError):
                    continue

                if method == "Network.requestWillBeSent":
                    request_id = params.get("requestId")
                    request = params.get("request", {})
                    if request_id:
                        request_details[request_id] = {
                            "method": request.get("method"),
                            "url": request.get("url"),
                            "postData": request.get("postData", ""),
                            "headers": request.get("headers", {}),
                        }
                    continue

                if method != "Network.responseReceived":
                    continue

                request_id = params.get("requestId")
                response = params.get("response", {})
                if not request_id or request_id in seen_response_ids:
                    continue

                seen_response_ids.add(request_id)
                url = response.get("url", "")
                mime_type = response.get("mimeType", "")
                status = response.get("status")
                request = request_details.get(request_id, {})
                post_data = request.get("postData", "") or ""
                operation_hint = ""

                if post_data:
                    match = re.search(
                        r"(?:friendly_name|operationName)=([^&]+)", post_data
                    )
                    if match:
                        operation_hint = match.group(1)
                if (operation_hint or request_id) != "AdLibrarySearchPaginationQuery":
                    continue

                if url_filter and url_filter not in url:
                    continue
                if mime_filter and mime_filter.lower() not in mime_type.lower():
                    continue
                if request_body_filter and request_body_filter not in post_data:
                    continue

                try:
                    body, was_base64 = read_response_body(active_driver, request_id)
                except RuntimeError as error:
                    print(f"Bỏ qua {url}: {error}")
                    continue

                body_text = decode_for_filter(body)

                if response_body_filter and response_body_filter not in body_text:
                    continue
                captured_response_bodies.append(body_text)
                emit_ads(body_text)
                print(f"Đã capture: {operation_hint or request_id}")

            time.sleep(0.1)

        print(f"capture {len(captured_response_bodies)} response.")
        if proxy_pool and active_proxy:
            proxy_pool.mark_success(active_proxy)
        return captured_response_bodies

    except Exception:
        if proxy_pool and active_proxy and not (
            callback_raised or stop_check_raised
        ):
            proxy_pool.mark_failure(active_proxy)
        raise

    finally:
        if active_driver is not None:
            active_driver.quit()
