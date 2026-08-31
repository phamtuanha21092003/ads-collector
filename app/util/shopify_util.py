from urllib.parse import urlparse

import dns.resolver
import requests
from app.util.format_url_util import canonical_product_url
import time

SHOPIFY_IPS = {
    "23.227.38.65",
}

RETRY_STATUS_CODES = {429, 500, 501, 502, 503}


def get_product_from_url(url, timeout=10, proxy=None, max_retries=5):
    product_url = canonical_product_url(url=url, include_json=True)
    if product_url is None:
        return None
    for attempt in range(max_retries + 1):
        proxy_config = {}
        if proxy:
            proxy_config = {
                "http": f"http://{proxy}",
                "https": f"http://{proxy}",
            }

        try:
            response = requests.get(
                product_url,
                timeout=timeout,
                headers={"User-Agent": "Mozilla/5.0"},
                proxies=proxy_config,
            )

            status = response.status_code

            # Success
            if status == 200:
                content_type = response.headers.get("Content-Type", "")

                if not content_type.startswith("application/json"):
                    return None

                payload = response.json()

                product = payload.get("product") if isinstance(payload, dict) else None

                if not isinstance(product, dict):
                    return None

                return product_url, product

            # Retry only these status codes
            if status in RETRY_STATUS_CODES:
                print(
                    f"product status={status}, "
                    f"attempt={attempt + 1}/{max_retries + 1}, "
                )

                if attempt < max_retries:
                    # 1s -> 2s -> 4s
                    time.sleep(2**attempt)
                    continue

            # Other status: don't retry
            print(f"product status={status}, no retry")
            return None

        except requests.RequestException as e:
            # Network errors / timeout -> retry
            print(
                f"product request error={e}, "
                f"attempt={attempt + 1}/{max_retries + 1}, "
            )

            if attempt < max_retries:
                time.sleep(2**attempt)
                continue

            return None

    return None


def extract_domain(url: str) -> str | None:
    if not url:
        return None
    value = url.strip()
    if not value.startswith(("http://", "https://")):
        value = f"https://{value}"
    host = (urlparse(value).hostname or "").rstrip(".").lower()
    return host or None


def is_shopify_domain(domain: str, timeout: int = 10) -> bool:
    if not domain.startswith(("http://", "https://")):
        domain = f"https://{domain}"

    parsed = urlparse(domain)
    host = parsed.hostname or ""

    if host.endswith(".myshopify.com"):
        return True

    try:
        answers = dns.resolver.resolve(host, "CNAME")

        for answer in answers:
            cname = str(answer.target).rstrip(".").lower()

            if cname.endswith(".myshopify.com") or cname.endswith(
                ".shops.myshopify.com"
            ):
                return True

    except Exception:
        pass

    try:
        answers = dns.resolver.resolve(host, "A")

        for answer in answers:
            if answer.address in SHOPIFY_IPS:
                return True

    except Exception:
        pass

    try:
        response = requests.get(
            domain,
            timeout=timeout,
            allow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0",
            },
        )

        html = response.text.lower()

        # Shopify response headers
        if any(header.lower() == "x-shopid" for header in response.headers):
            return True

        signatures = (
            "cdn.shopify.com",
            "shopify.theme",
            "shopify.shop",
            "shopify-payment-button",
        )

        if any(signature in html for signature in signatures):
            return True

        product_response = requests.get(
            response.url.rstrip("/") + "/products.json?limit=1",
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0"},
        )

        if product_response.status_code == 200 and product_response.headers.get(
            "Content-Type", ""
        ).startswith("application/json"):
            data = product_response.json()

            if isinstance(data, dict) and "products" in data:
                return True

    except Exception:
        pass

    return False
