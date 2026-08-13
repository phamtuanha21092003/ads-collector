from urllib.parse import urlparse, urlunparse


def canonical_product_url(url: str, include_json: bool = False) -> str | None:
    """Return a clean Shopify product URL, optionally with its `.json` suffix."""
    if not isinstance(url, str) or not url.strip():
        return None
    value = url.strip()
    if not value.startswith(("http://", "https://")):
        value = f"https://{value}"
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.netloc:
        return None
    path = parsed.path.rstrip("/")
    if not path.startswith("/products/"):
        return None
    handle = path.removeprefix("/products/")
    if not handle or "/" in handle:
        return None
    if include_json and not handle.endswith(".json"):
        path = f"{path}.json"
    elif not include_json:
        path = path.removesuffix(".json")
    return urlunparse(("https", parsed.netloc, path, "", "", ""))
