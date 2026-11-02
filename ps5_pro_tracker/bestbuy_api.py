"""Best Buy's official Products API (free key from https://developer.bestbuy.com).

Much more reliable than scraping bestbuy.com, which blocks most automated
traffic. Used automatically when the ``BESTBUY_API_KEY`` environment variable
is set.
"""

from __future__ import annotations

import requests

from .detect import IN_STOCK, OUT_OF_STOCK
from .config import Config

API = "https://api.bestbuy.com/v1/products({query})"
FIELDS = "sku,name,salePrice,onlineAvailability,orderable,url,addToCartUrl,inStoreAvailability"


def search(api_key: str, config: Config, timeout: float = 20) -> list[dict]:
    """Return ``[{url, title, price, status, seller}, ...]`` for PS5 Pro items."""
    items: list[dict] = []
    seen: set[int] = set()
    for terms in (("playstation", "5", "pro"), ("ps5", "pro")):
        query = "&".join(f"search={t}" for t in terms)
        r = requests.get(
            API.format(query=query),
            params={"apiKey": api_key, "format": "json", "show": FIELDS, "pageSize": 50},
            timeout=timeout,
        )
        r.raise_for_status()
        for p in r.json().get("products", []):
            if p.get("sku") in seen or not config.is_ps5_pro(p.get("name")):
                continue
            seen.add(p.get("sku"))
            orderable = str(p.get("orderable", "")).lower()
            available = bool(p.get("onlineAvailability")) or orderable in ("available", "preorder")
            items.append(
                {
                    "url": p.get("url") or f"https://www.bestbuy.com/site/{p.get('sku')}.p?skuId={p.get('sku')}",
                    "title": p.get("name"),
                    "price": p.get("salePrice"),
                    "status": IN_STOCK if available else OUT_OF_STOCK,
                    "seller": "Best Buy",
                    "reason": f"Best Buy API (onlineAvailability={p.get('onlineAvailability')}, orderable={p.get('orderable')})",
                }
            )
    return items
