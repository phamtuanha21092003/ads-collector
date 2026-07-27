from urllib.parse import urlparse

import dns.resolver
import requests

SHOPIFY_IPS = {
    "23.227.38.65",
}


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

            if (
                cname.endswith(".myshopify.com")
                or cname.endswith(".shops.myshopify.com")
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

        if (
            product_response.status_code == 200
            and product_response.headers.get("Content-Type", "").startswith("application/json")
        ):
            data = product_response.json()

            if isinstance(data, dict) and "products" in data:
                return True

    except Exception:
        pass

    return False
