"""Find PS5 Pro product links on a retailer's search-results page."""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from .config import Config

# URL shapes that are product detail pages on the supported retailers.
_PRODUCT_URL = re.compile(
    r"(/site/.+\.p(\?|$)|/product/|/ip/|/p/[A-Z0-9-]+|/dp/[A-Z0-9]{10}|/gp/product/|"
    r"/products/|/buy-consoles/|\.product\.\d+\.html|/prod\d+|/p/[a-z0-9-]+/(prod)?\d+|"
    r"/[A-Z0-9]{2}-\d{3}-[A-Z0-9]{3}|/product\.aspx|/video-games/.+/\d+\.html)",
    re.I,
)


def _canonical(url: str) -> str:
    """Strip tracking query strings so the same product is only checked once."""
    p = urlparse(url)
    keep = ""
    if p.query:
        # Best Buy needs skuId; Newegg item pages need Item; Costco/others don't need any.
        params = [kv for kv in p.query.split("&") if kv.split("=")[0].lower() in ("skuid", "item")]
        keep = "&".join(params)
    path = re.sub(r"/ref=.*$", "", p.path)  # Amazon
    return urlunparse((p.scheme, p.netloc, path, "", keep, ""))


def find_candidates(html: str, base_url: str, config: Config, limit: int) -> list[tuple[str, str]]:
    """Return ``[(url, link_text), ...]`` for links that look like a PS5 Pro listing."""
    soup = BeautifulSoup(html, "lxml")
    host = urlparse(base_url).netloc.split(":")[0].removeprefix("www.")
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith(("javascript:", "#", "mailto:")):
            continue
        url = urljoin(base_url, href)
        link_host = urlparse(url).netloc.removeprefix("www.")
        if host not in link_host:  # stay on the retailer's own site
            continue
        if not _PRODUCT_URL.search(urlparse(url).path + ("?" + urlparse(url).query if urlparse(url).query else "")):
            continue
        text = " ".join(
            filter(None, [a.get_text(" ", strip=True), a.get("title"), a.get("aria-label")])
        )
        img = a.find("img")
        if img is not None and img.get("alt"):
            text += " " + img["alt"]
        label = text.strip() or urlparse(url).path
        if not (config.is_ps5_pro(text) or config.is_ps5_pro(urlparse(url).path)):
            continue
        # A link whose text explicitly names an accessory is still excluded
        # even if the slug matched.
        if text.strip() and not config.is_ps5_pro(text) and any(p.search(text) for p in config.exclude):
            continue
        key = _canonical(url)
        if key in seen:
            continue
        seen.add(key)
        out.append((key, label[:150]))
        if len(out) >= limit:
            break
    return out
