"""Work out from a product page's HTML whether an item can be bought.

Detection is layered, most reliable first:

1. schema.org structured data (JSON-LD ``Product``/``Offer`` blocks), which
   almost every large retailer publishes for search engines.
2. ``<meta>`` availability tags (``product:availability``, ``og:availability``,
   ``itemprop="availability"``).
3. The purchase buttons on the page ("Add to Cart" vs "Sold Out").

Each layer returns IN_STOCK, OUT_OF_STOCK or None (no opinion).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from bs4 import BeautifulSoup

IN_STOCK = "IN_STOCK"
OUT_OF_STOCK = "OUT_OF_STOCK"
UNKNOWN = "UNKNOWN"
BLOCKED = "BLOCKED"
NOT_FOUND = "NOT_FOUND"
ERROR = "ERROR"


@dataclass
class PageResult:
    status: str
    title: str | None = None
    price: float | None = None
    seller: str | None = None
    reason: str = ""
    signals: list[str] = field(default_factory=list)


_SCHEMA_IN = ("instock", "instoreonly", "limitedavailability", "onlineonly", "preorder", "presale")
_SCHEMA_OUT = ("outofstock", "soldout", "discontinued", "backorder")

_BLOCK_MARKERS = (
    "robot or human",
    "are you a robot",
    "verify you are a human",
    "access denied",
    "request unsuccessful. incapsula",
    "px-captcha",
    "captcha-delivery",
    "enter the characters you see below",
    "sorry, we just need to make sure you're not a robot",
    "pardon our interruption",
    "unusual traffic",
    "please enable js and disable any ad blocker",
)

_BUY_PATTERNS = re.compile(
    r"^\s*(add to (cart|bag|basket)|buy now|pre-?order( now)?|ship it|pick up today|"
    r"add for (shipping|delivery|pickup))\s*$",
    re.I,
)
_OOS_PATTERNS = re.compile(
    r"\b(sold out|out of stock|currently unavailable|unavailable|coming soon|"
    r"notify me|email me when available|join (the )?waitlist|not available|"
    r"check stores|no longer available|temporarily out of stock)\b",
    re.I,
)
_PAGE_OOS_PHRASES = re.compile(
    r"(currently unavailable\.?\s*we don't know when|this item is (currently )?out of stock|"
    r"out of stock online|sold out online|temporarily out of stock|"
    r"we don't know when or if this item will be back)",
    re.I,
)


def looks_blocked(html: str) -> bool:
    head = html[:20000].lower()
    if len(html) < 2500 and ("captcha" in head or "denied" in head or "blocked" in head):
        return True
    return any(m in head for m in _BLOCK_MARKERS)


def _iter_jsonld(soup: BeautifulSoup) -> Iterable[dict[str, Any]]:
    for tag in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        raw = tag.string or tag.get_text() or ""
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Some sites put several objects or trailing commas in one tag.
            try:
                data = json.loads(re.sub(r",\s*([}\]])", r"\1", raw))
            except json.JSONDecodeError:
                continue
        yield from _walk(data)


def _walk(node: Any) -> Iterable[dict[str, Any]]:
    if isinstance(node, dict):
        yield node
        for v in node.values():
            if isinstance(v, (dict, list)):
                yield from _walk(v)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _types(node: dict[str, Any]) -> set[str]:
    t = node.get("@type")
    if isinstance(t, list):
        return {str(x).lower() for x in t}
    return {str(t).lower()} if t else set()


def _norm_availability(value: Any) -> str | None:
    if not value:
        return None
    v = re.sub(r"[^a-z]", "", str(value).lower().rsplit("/", 1)[-1])
    if any(v.startswith(x) for x in _SCHEMA_OUT):
        return OUT_OF_STOCK
    if any(v.startswith(x) for x in _SCHEMA_IN):
        return IN_STOCK
    return None


def _to_price(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = re.search(r"\d[\d,]*(?:\.\d+)?", str(value))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _seller_name(offer: dict[str, Any]) -> str | None:
    seller = offer.get("seller") or offer.get("offeredBy")
    if isinstance(seller, dict):
        return seller.get("name")
    if isinstance(seller, str):
        return seller
    return None


def from_structured_data(soup: BeautifulSoup) -> PageResult | None:
    """Read the main Product's offers from JSON-LD."""
    products = [n for n in _iter_jsonld(soup) if "product" in _types(n) or "productgroup" in _types(n)]
    if not products:
        return None
    # The first Product on a page is the main item; later ones are usually
    # "customers also bought" carousels.
    product = products[0]
    title = product.get("name")
    offers = product.get("offers")
    if isinstance(offers, dict) and "offers" in offers and isinstance(offers["offers"], list):
        offers = offers["offers"]  # AggregateOffer
    if isinstance(offers, dict):
        offers = [offers]
    if not isinstance(offers, list):
        offers = []

    statuses: list[str] = []
    price = None
    seller = None
    for offer in offers:
        if not isinstance(offer, dict):
            continue
        s = _norm_availability(offer.get("availability"))
        if s:
            statuses.append(s)
        p = _to_price(offer.get("price") or offer.get("lowPrice"))
        if p and (price is None or (s == IN_STOCK)):
            price = p
        seller = seller or _seller_name(offer)

    if not statuses:
        return PageResult(UNKNOWN, title=title, price=price, seller=seller, reason="json-ld without availability")
    status = IN_STOCK if IN_STOCK in statuses else OUT_OF_STOCK
    return PageResult(status, title=title, price=price, seller=seller,
                      reason="json-ld availability", signals=[f"jsonld:{s}" for s in statuses])


def from_meta(soup: BeautifulSoup) -> str | None:
    for attrs in (
        {"property": re.compile(r"(product|og):availability", re.I)},
        {"name": re.compile(r"(product|og):availability", re.I)},
        {"itemprop": "availability"},
    ):
        for tag in soup.find_all(["meta", "link"], attrs=attrs):
            s = _norm_availability(tag.get("content") or tag.get("href"))
            if s:
                return s
    return None


_NOT_MAIN_PRODUCT = re.compile(
    r"carousel|recommend|similar|related|sponsor|also[-_ ]?(bought|viewed)|shoveler|"
    r"footer|header|\bnav\b|navigation|minicart|mini-cart|upsell|cross-?sell|accessor",
    re.I,
)


def _in_side_content(el) -> bool:
    """True when a button sits in a carousel / recommendation / header block."""
    for parent in el.parents:
        if parent.name in ("header", "footer", "nav"):
            return True
        ident = " ".join(parent.get("class", []) or []) + " " + str(parent.get("id", "") or "")
        if ident.strip() and _NOT_MAIN_PRODUCT.search(ident):
            return True
    return False


def from_buttons(soup: BeautifulSoup) -> tuple[str | None, list[str]]:
    """Look at purchase buttons. An enabled buy button beats everything."""
    buy, oos = [], []
    for el in soup.find_all(["button", "input", "a"]):
        if _in_side_content(el):
            continue
        if el.name == "input":
            text = el.get("value") or ""
        else:
            text = el.get_text(" ", strip=True)
        text = (text or el.get("aria-label") or "").strip()
        if not text or len(text) > 60:
            continue
        disabled = (
            el.has_attr("disabled")
            or str(el.get("aria-disabled", "")).lower() == "true"
            or "disabled" in " ".join(el.get("class", [])).lower()
        )
        if _BUY_PATTERNS.match(text):
            (oos if disabled else buy).append(text)
        elif _OOS_PATTERNS.search(text):
            oos.append(text)
    if buy:
        return IN_STOCK, buy
    if oos:
        return OUT_OF_STOCK, oos
    return None, []


def _page_title(soup: BeautifulSoup) -> str | None:
    for sel in ("h1", "meta[property='og:title']", "title"):
        el = soup.select_one(sel)
        if el is None:
            continue
        text = el.get("content") if el.name == "meta" else el.get_text(" ", strip=True)
        if text:
            return text.strip()
    return None


_SELLER_TEXT = re.compile(
    r"(?:sold (?:and shipped )?by|ships from and sold by|sold & shipped by|seller:?)\s*"
    r"([A-Za-z0-9][\w .&'\-]{1,40})",
    re.I,
)


_SELLER_JSON = re.compile(r'"(?:sellerDisplayName|sellerName|soldBy|merchantName)"\s*:\s*"([^"]{1,60})"')


def _seller_from_json(html: str) -> str | None:
    """Sellers in the page's embedded app data (e.g. Walmart's __NEXT_DATA__)."""
    m = _SELLER_JSON.search(html)
    return m.group(1).strip() if m else None


def _seller_from_text(text: str) -> str | None:
    m = _SELLER_TEXT.search(text)
    return m.group(1).strip(" .") if m else None


def analyse_page(html: str) -> PageResult:
    if looks_blocked(html):
        return PageResult(BLOCKED, reason="bot protection / captcha page")

    soup = BeautifulSoup(html, "lxml")
    structured = from_structured_data(soup)
    meta = from_meta(soup)
    button, button_texts = from_buttons(soup)
    text = soup.get_text(" ", strip=True)

    result = structured or PageResult(UNKNOWN)
    result.title = result.title or _page_title(soup)
    result.seller = result.seller or _seller_from_json(html) or _seller_from_text(text)
    if result.price is None:
        price_meta = soup.find("meta", attrs={"property": re.compile(r"(product|og):price:amount", re.I)}) \
            or soup.find(attrs={"itemprop": "price"})
        if price_meta is not None:
            result.price = _to_price(price_meta.get("content") or price_meta.get_text())

    if meta:
        result.signals.append(f"meta:{meta}")
    if button:
        result.signals.append(f"button:{button}:{'|'.join(button_texts[:3])}")

    if result.status in (IN_STOCK, OUT_OF_STOCK):
        # Structured data is authoritative, but a page that shows "Sold Out"
        # and no buy button while JSON-LD says InStock is usually stale
        # JSON-LD; trust what the shopper would see.
        if result.status == IN_STOCK and button == OUT_OF_STOCK:
            result.status = OUT_OF_STOCK
            result.reason = "json-ld says in stock but page buttons say sold out"
        return result

    if meta:
        result.status, result.reason = meta, "meta availability tag"
    elif button:
        result.status, result.reason = button, "purchase buttons"
    elif _PAGE_OOS_PHRASES.search(text):
        result.status, result.reason = OUT_OF_STOCK, "out-of-stock text on page"
    else:
        result.status, result.reason = UNKNOWN, "no availability signal found"
    return result
