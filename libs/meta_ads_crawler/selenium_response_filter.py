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

from app.model.proxy_log_model import ProxyLogStatus
from .meta_ads_embedded_json import getAds
import random
from .meta_ads_embedded_json import normalize_ad
from app.exceptions import ReachThresholdReached, RateLimitError

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
    enable_performance_logging: bool = False,
) -> webdriver.Chrome:
    options = webdriver.ChromeOptions()
    # if proxy:
    #     options.add_argument(f"--proxy-server=http://27.72.102.142:5199")
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
    options.add_argument("--headless=false")
    options.add_argument("--no-sandbox")
    options.add_argument("--window-size=1440,1200")
    options.add_argument("--lang=en-US")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-dev-shm-usage")

    # Enable performance logging to capture network requests
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

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
            on_request_log=on_request_log,
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
        on_request_log: OnRequestLogCallback | None = None,
    ) -> list[str | dict[str, Any]]:
        """Capture network responses that match the supplied filters."""

        active_driver: webdriver.Chrome | None = None
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
        graphql_request_data = None
        try:
            time.sleep(1.2)
            # Enable performance logging to capture network requests
            active_driver = create_driver(proxy=proxy, enable_performance_logging=True)
            print(f"Open Page: {page_url}")
            active_driver.get(page_url)

            # Wait for page to load
            time.sleep(10)

            # Scroll down to trigger GraphQL API calls
            active_driver.execute_script(
                "window.scrollTo(0, document.body.scrollHeight);"
            )
            time.sleep(2)

            # Extract GraphQL request data from performance logs
            graphql_request_data = {
                "request_headers": {},
                "request_body": None,
                "cookies": {},
                "url": None,
            }

            # Get cookies from browser
            all_cookies = active_driver.get_cookies()
            cookies_dict = {cookie["name"]: cookie["value"] for cookie in all_cookies}
            graphql_request_data["cookies"] = cookies_dict

            # Parse performance logs to find GraphQL requests
            logs = active_driver.get_log("performance")
            for entry in logs:
                try:
                    log_data = json.loads(entry["message"])
                    message = log_data.get("message", {})
                    method = message.get("method", "")

                    # Capture Network.requestWillBeSent for GraphQL requests
                    if method == "Network.requestWillBeSent":
                        request = message.get("params", {}).get("request", {})
                        url = request.get("url", "")
                        if "/api/graphql" in url:
                            post_data = request.get("postData", "")

                            # Check if this is AdLibrarySearchPaginationQuery
                            if (
                                post_data
                                and "AdLibrarySearchPaginationQuery" in post_data
                            ):
                                graphql_request_data["url"] = url
                                graphql_request_data["request_headers"] = request.get(
                                    "headers", {}
                                )
                                graphql_request_data["request_body"] = post_data
                                print(
                                    f"Captured AdLibrarySearchPaginationQuery GraphQL request to: {url}"
                                )
                                break

                except (json.JSONDecodeError, KeyError):
                    continue

            # Get initial ads from page
            response = getAds(active_driver)
            ads = response.ads if hasattr(response, "ads") else []
            cursor = response.cursor if hasattr(response, "cursor") else None

            for ad in ads:
                emit_ads(ad)
            if not cursor:
                return ads

            # Use captured GraphQL request data to make authenticated requests
            if graphql_request_data["url"] and graphql_request_data["request_body"]:
                print("Using captured GraphQL request data for API calls...")
                from uuid6 import uuid7

                request_id = str(uuid7())
                cookies = graphql_request_data["cookies"]

                # Use the captured request headers
                headers = graphql_request_data["request_headers"]

                # Parse the captured request body to extract variables
                captured_body = graphql_request_data["request_body"]

                processing_max_retries = 5
                processing_retry_count = 0
                processing_retry_delay = 2
                session: Any | None = None
                body_params = dict(parse_qsl(captured_body))
                body_params["variables"] = json.loads(body_params["variables"])
                counter = int(body_params["__req"])
                # active_driver.quit()
                while cursor is not None:
                    time.sleep(1.2)
                    try:
                        # Parse captured body and update cursor

                        # Update cursor in the body
                        body_params["variables"]["cursor"] = cursor
                        body_params["__req"] = self._encode_request_id(counter)
                        counter = counter + 1
                        # Rebuild the body
                        updated_body = urlencode(
                            {
                                **body_params,
                                "variables": json.dumps(
                                    body_params["variables"], separators=(",", ":")
                                ),
                            }
                        )

                        # Make GraphQL request using captured headers and body with retry on rate limit
                        max_retries = 10
                        retry_delay = 2  # Initial delay in seconds

                        for retry_count in range(max_retries):
                            from curl_cffi.requests import Session as CffiSession

                            request_id = str(uuid7())
                            # Each GraphQL attempt gets its own browser/TLS session.
                            session = CffiSession(impersonate="chrome")
                            for name, value in cookies.items():
                                session.cookies.set(
                                    name, value, domain=".facebook.com", path="/"
                                )
                            # session.proxies = {
                            #     "http": f"http://{request_id}:x@{proxy}",
                            #     "https": f"http://{request_id}:x@{proxy}",
                            # }
                            try:
                                api_response = session.post(
                                    graphql_request_data["url"],
                                    headers=headers,
                                    data=updated_body,
                                    timeout=30,
                                )
                                if on_request_log is not None:
                                    on_request_log(
                                        request_id=request_id,
                                        headers=headers,
                                        request_body=updated_body,
                                    )

                            except Exception as error:
                                session.close()
                                if on_request_log is not None:
                                    on_request_log(
                                        request_id=request_id,
                                        status=ProxyLogStatus.FAILED.value,
                                        err={"message": str(error)},
                                    )
                                raise

                            # Facebook can report a rate limit in the GraphQL
                            # payload even when the HTTP status is 200.
                            retryable_statuses = {429, 500, 502, 503, 504}
                            is_retryable = (
                                api_response.status_code in retryable_statuses
                            )
                            errors: list[dict[str, Any]] = []

                            if api_response.status_code == 200:
                                try:
                                    response_text = api_response.text.removeprefix(
                                        "for (;;);"
                                    )
                                    response_data = json.loads(response_text)
                                    errors = response_data.get("errors", []) or []
                                    is_retryable = any(
                                        error.get("code") == 1675004
                                        or "rate limit"
                                        in error.get("message", "").lower()
                                        or "temporarily unavailable"
                                        in error.get("message", "").lower()
                                        for error in errors
                                    )
                                    error = errors[0] if errors else None
                                    if on_request_log is not None:
                                        on_request_log(
                                            request_id=request_id,
                                            body=api_response.text,
                                            status_code=api_response.status_code,
                                            status=(
                                                ProxyLogStatus.FAILED.value
                                                if is_retryable or error
                                                else ProxyLogStatus.SUCCESS.value
                                            ),
                                            err=(
                                                {"err": "Rate limit"}
                                                if is_retryable
                                                else error
                                            ),
                                        )

                                except json.JSONDecodeError:
                                    # A malformed successful response can be transient.
                                    is_retryable = True

                            session.close()

                            if not is_retryable:
                                break

                            if retry_count == max_retries - 1:
                                print(
                                    f"GraphQL request still failed after {max_retries} "
                                    f"attempts (HTTP {api_response.status_code})."
                                )
                                break

                            retry_after = api_response.headers.get("Retry-After")
                            try:
                                wait_time = (
                                    float(retry_after) if retry_after else retry_delay
                                )
                            except ValueError:
                                wait_time = retry_delay

                            print(
                                f"Facebook GraphQL retryable error "
                                f"(HTTP {api_response.status_code}); retrying in "
                                f"{wait_time:.1f}s (attempt {retry_count + 1}/"
                                f"{max_retries})"
                            )
                            time.sleep(wait_time + random.uniform(0, 1))
                            retry_delay = min(retry_delay * 2, 60)
                            continue
                        else:
                            # All retries exhausted
                            break

                        if api_response.status_code == 200:
                            response_text = api_response.text
                            if response_text.startswith("for (;;);"):
                                response_text = response_text[9:]

                            response_data = json.loads(response_text)
                            # Parse response
                            results = (
                                response_data.get("data", {})
                                .get("ad_library_main", {})
                                .get("search_results_connection", {})
                            )

                            if not results:
                                results = (
                                    response_data.get("data", {})
                                    .get("adLibraryMain", {})
                                    .get("searchResultsConnection", {})
                                )

                            if not results:
                                results = response_data.get("data", {})

                            edges = results.get("edges", [])
                            page_info = results.get("page_info", {}) or results.get(
                                "pageInfo", {}
                            )
                            # Get next cursor
                            next_cursor = None
                            if page_info.get("has_next_page") or page_info.get(
                                "hasNextPage"
                            ):
                                next_cursor = page_info.get(
                                    "end_cursor"
                                ) or page_info.get("endCursor")

                            cursor = next_cursor

                            # Extract ads
                            ads_count = 0
                            for edge in edges:
                                node = edge.get("node", edge)
                                if node:
                                    collated = node.get("collated_results", [])
                                    for ad_data in collated:
                                        snapshot = ad_data.get("snapshot") or {}
                                        flattened = dict(ad_data)
                                        ads_count = ads_count + 1
                                        if snapshot:
                                            for key, value in snapshot.items():
                                                if key not in flattened:
                                                    flattened[key] = value
                                        ads.append(flattened)
                                        emit_ads(normalize_ad(flattened))
                            print(
                                f"Facebook GraphQL successfully "
                                f"(HTTP {api_response.status_code}) "
                                f"Result: {ads_count} "
                            )
                            # Reset the retry budget once this page has been
                            # successfully parsed and emitted.
                            processing_retry_count = 0
                            processing_retry_delay = 2

                            if cursor is None:
                                break
                        else:
                            print(f"GraphQL request failed: {api_response.status_code}")
                            break
                    except ReachThresholdReached as ex:
                        break
                    except Exception as e:
                        processing_retry_count += 1
                        if processing_retry_count >= processing_max_retries:
                            print(
                                "Error processing GraphQL response after "
                                f"{processing_max_retries} attempts: {e}"
                            )
                            break

                        wait_time = processing_retry_delay
                        print(
                            f"Error processing GraphQL response: {e}. Retrying in "
                            f"{wait_time:.1f}s (attempt {processing_retry_count}/"
                            f"{processing_max_retries})"
                        )
                        time.sleep(wait_time + random.uniform(0, 1))
                        processing_retry_delay = min(processing_retry_delay * 2, 60)
                        continue

                if session is not None:
                    session.close()
            # else:
            #     print(
            #         "No GraphQL request data captured, falling back to MetaAdsClient..."
            #     )
            #     # Fallback to MetaAdsClient if no captured data
            #     client = MetaAdsClient(proxy=proxy, html=html, max_retries=10)
            #     while cursor is not None:
            #         time.sleep(1.2)
            #         response, cursor = client.search_ads(
            #             cursor=cursor,
            #             query=query,
            #             active_status=status,
            #             ad_type=ad_type,
            #             start_date=start_date,
            #             end_date=end_date,
            #             sort_mode="SORT_BY_TOTAL_IMPRESSIONS",
            #             on_request_log=on_request_log,
            #         )
            #         if response and isinstance(response, dict):
            #             print("response oke")
            #             response_ads = response.get("ads", [])
            #             ads.extend(response_ads)
            #             for ad in response_ads:
            #                 emit_ads(normalize_ad(ad))
            #             print(f"Captured {len(response_ads)} ads. Total: {len(ads)}")

            print(f"Total captured {len(ads)} ads.")
            return ads
        except Exception:
            raise
        finally:
            if active_driver is not None:
                active_driver.quit()

    def _encode_request_id(self, counter: int) -> str:
        """Encode the request counter as a base-36 string."""
        if counter < 10:
            return str(counter)
        chars = "0123456789abcdefghijklmnopqrstuvwxyz"
        result = ""
        while counter:
            result = chars[counter % 36] + result
            counter //= 36
        return result
