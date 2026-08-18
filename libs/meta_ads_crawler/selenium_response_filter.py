import base64
from email import message
import json
import re
import time
from collections.abc import Callable
from datetime import date
from typing import Any
from urllib.parse import parse_qsl, unquote_plus, urlencode

from selenium import webdriver
from selenium.common.exceptions import WebDriverException

from libs.meta_ads_collector.exceptions import RateLimitError
from libs.meta_ads_collector.proxy_pool import ProxyPool
from .meta_ads_embedded_json import extract_ads_from_scripts, getAds
import random

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
    headless: bool,
    proxy: str | None = None,
) -> webdriver.Chrome:
    options = webdriver.ChromeOptions()
    if proxy:
        sanitized = proxy
        if "@" in proxy:
            parts = proxy.split("@")
            scheme = ""
            if "://" in parts[0]:
                scheme = parts[0].split("://")[0] + "://"
            sanitized = f"{scheme}{parts[-1]}"
        print(f"Creating Chrome driver using proxy: {sanitized}")
        options.add_argument(f"--proxy-server={proxy}")
    if headless:
        print("Running Chrome in headless mode.")
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

    options.add_argument(f"--user-agent={user_agent}")
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
        stop_check: Callable[[], None] | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
        filter_config: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        capture_duration_seconds: float | None = 180,
        auto_scroll: bool = True,
        scroll_interval_seconds: float = 3.0,
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
            on_request_log=on_request_log,
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
    page_url: str,
    mime_filter: str,
    request_body_filter: str,
    response_body_filter: str,
    duration_seconds: float | None,
    headless: bool,
    auto_scroll: bool,
    scroll_interval_seconds: float,
    proxy: str = None,
    on_ad: OnAdCallback | None = None,
    on_request_log: OnRequestLogCallback | None = None,
    stop_check: Callable[[], None] | None = None,
) -> list[str | dict[str, Any]]:
    """Capture network responses that match the supplied filters."""
    url_filter = "/api/graphql/"

    active_driver: webdriver.Chrome | None = None

    request_details: dict[str, dict[str, Any]] = {}
    response_details: dict[str, dict[str, Any]] = {}
    loading_finished_ids: set[str] = set()
    captured_response_bodies: list[str | dict[str, Any]] = []
    processed_response_ids: set[str] = set()
    logged_request_ids: set[str] = set()
    emitted_ad_ids: set[str] = set()
    callback_raised = False
    stop_check_raised = False
    is_break = False

    def emit_ads(
        response_body: str | dict[str, Any],
    ) -> None:
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
                    on_ad(ad, page_url)
                except Exception:
                    callback_raised = True
                    raise

    def operation_name(request: dict[str, Any]) -> str:
        post_data = request.get("postData", "") or ""
        match = re.search(
            r"(?:^|&)(?:fb_api_req_friendly_name|friendly_name|operationName)=([^&]+)",
            post_data,
        )
        if match:
            return unquote_plus(match.group(1))

        headers = request.get("headers", {}) or {}
        for name, value in headers.items():
            if name.lower() == "x-fb-friendly-name":
                return str(value)
        return ""

    def is_target_request(request: dict[str, Any]) -> bool:
        request_url = request.get("url", "") or ""
        post_data = request.get("postData", "") or ""
        request_operation = operation_name(request)
        return (not url_filter or url_filter in request_url) and (
            not request_body_filter
            or request_body_filter in post_data
            or request_operation == request_body_filter
        )

    def emit_request_log(
        request_id: str,
        *,
        error: dict[str, Any] | None = None,
    ) -> None:
        if on_request_log is None or request_id in logged_request_ids:
            return
        request = request_details.get(request_id)
        if request is None or not is_target_request(request):
            return
        response = response_details.get(request_id)
        if error is None and response is None:
            return
        logged_request_ids.add(request_id)
        started = request.get("started_at")
        duration_ms = (
            int((time.monotonic() - started) * 1000) if started is not None else None
        )
        status_code = (response or {}).get("status")
        on_request_log(
            url=request.get("url") or (response or {}).get("url") or "",
            method=request.get("method"),
            status_code=status_code,
            duration_ms=duration_ms,
            error=error,
        )

    def scroll_to_pagination_trigger() -> bool:
        """Scroll to the current list end, which triggers Meta pagination."""
        if active_driver is None:
            return False
        return bool(active_driver.execute_script("""
                const root = document.documentElement;
                const before = window.scrollY;
                window.scrollTo(0, root.scrollHeight);
                return window.scrollY > before;
                """))

    def capture_completed_response(request_id: str) -> None:
        """Capture only after the matched request has finished loading."""
        if (
            request_id in processed_response_ids
            or request_id not in loading_finished_ids
            or active_driver is None
        ):
            return

        request = request_details.get(request_id)
        response = response_details.get(request_id)
        if request is None or response is None:
            return

        if not is_target_request(request):
            processed_response_ids.add(request_id)
            return

        emit_request_log(request_id)

        url = response.get("url", "")
        mime_type = response.get("mimeType", "")
        if url_filter and url_filter not in url:
            processed_response_ids.add(request_id)
            return
        if mime_filter and mime_filter.lower() not in mime_type.lower():
            processed_response_ids.add(request_id)
            return

        try:
            body, _ = read_response_body(active_driver, request_id)
        except RuntimeError as error:
            print(f"Skip {url}: {error}")
            processed_response_ids.add(request_id)
            return

        processed_response_ids.add(request_id)
        body_text = decode_for_filter(body)
        if response_body_filter and response_body_filter not in body_text:
            return
        raise_for_graphql_error(body_text)
        captured_response_bodies.append(body_text)
        emit_ads(body_text)

    try:
        active_driver = create_driver(proxy=proxy, headless=headless)
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
        waiting_for_graphql = False
        graphql_response_finished_since_scroll = False
        graphql_wait_started_at: float | None = None
        while not is_break and (
            duration_seconds is None or time.monotonic() - started_at < duration_seconds
        ):
            if stop_check is not None:
                try:
                    stop_check()
                except Exception:
                    stop_check_raised = True
                    raise
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
                            "started_at": time.monotonic(),
                        }
                        capture_completed_response(request_id)
                    continue

                if method == "Network.responseReceived":
                    request_id = params.get("requestId")
                    if request_id:
                        response_details[request_id] = params.get("response", {})
                        capture_completed_response(request_id)
                    continue

                if method == "Network.loadingFailed":
                    request_id = params.get("requestId")
                    if request_id:
                        error_text = params.get("errorText") or "loadingFailed"
                        emit_request_log(
                            request_id,
                            error={
                                "message": error_text,
                                "type": "loadingFailed",
                            },
                        )
                    continue

                if method != "Network.loadingFinished":
                    continue

                request_id = params.get("requestId")
                if request_id:
                    loading_finished_ids.add(request_id)
                    emit_request_log(request_id)
                    capture_completed_response(request_id)
                    if (
                        waiting_for_graphql
                        and request_id in processed_response_ids
                        and is_target_request(request_details.get(request_id, {}))
                    ):
                        graphql_response_finished_since_scroll = True

            now = time.monotonic()
            if (
                waiting_for_graphql
                and not graphql_response_finished_since_scroll
                and graphql_wait_started_at is not None
                and now - graphql_wait_started_at >= 60
            ):
                print(
                    "No completed pagination GraphQL response within 60 seconds; "
                    "stopping crawl."
                )
                break

            if (
                auto_scroll
                and not is_break
                and now >= next_scroll_at
                and (not waiting_for_graphql or graphql_response_finished_since_scroll)
            ):
                # Wait for one completed pagination response before scrolling again.
                did_scroll = scroll_to_pagination_trigger()
                if not did_scroll:
                    print("Reached the end of the scrollable list; stopping crawl.")
                    break
                waiting_for_graphql = True
                graphql_response_finished_since_scroll = False
                graphql_wait_started_at = now
                next_scroll_at = now + scroll_interval_seconds

            time.sleep(0.1)

        print(f"capture {len(captured_response_bodies)} response.")
        return captured_response_bodies

    except Exception:
        raise

    finally:
        if active_driver is not None:
            active_driver.quit()
