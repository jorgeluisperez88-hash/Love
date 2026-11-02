import json
from pathlib import Path

import pytest

from ps5_pro_tracker import tracker
from ps5_pro_tracker.config import Retailer, load_config
from ps5_pro_tracker.detect import BLOCKED, IN_STOCK, OUT_OF_STOCK, UNKNOWN, analyse_page
from ps5_pro_tracker.discover import find_candidates
from ps5_pro_tracker.fetch import Response

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def config():
    return load_config(ROOT / "retailers.yaml")


def jsonld(availability, name="PlayStation 5 Pro Console", price="899.99", seller=None):
    offer = {"@type": "Offer", "price": price, "priceCurrency": "USD",
             "availability": f"https://schema.org/{availability}"}
    if seller:
        offer["seller"] = {"@type": "Organization", "name": seller}
    data = {"@context": "https://schema.org", "@type": "Product", "name": name, "offers": offer}
    return f'<script type="application/ld+json">{json.dumps(data)}</script>'


def page(body, head=""):
    return f"<html><head><title>Store</title>{head}</head><body>{body}{'<p>filler</p>' * 400}</body></html>"


# --- detection -------------------------------------------------------------

def test_jsonld_in_stock():
    r = analyse_page(page("<h1>PS5 Pro</h1><button>Add to Cart</button>", jsonld("InStock")))
    assert r.status == IN_STOCK
    assert r.price == 899.99
    assert r.title == "PlayStation 5 Pro Console"


def test_jsonld_out_of_stock():
    r = analyse_page(page("<h1>PS5 Pro</h1>", jsonld("OutOfStock")))
    assert r.status == OUT_OF_STOCK


def test_jsonld_in_stock_but_sold_out_button_trusts_page():
    r = analyse_page(page("<button disabled>Add to Cart</button><button>Sold Out</button>", jsonld("InStock")))
    assert r.status == OUT_OF_STOCK


def test_preorder_counts_as_buyable():
    assert analyse_page(page("", jsonld("PreOrder"))).status == IN_STOCK


def test_buttons_only():
    assert analyse_page(page("<h1>PS5 Pro</h1><button>Add to Cart</button>")).status == IN_STOCK
    assert analyse_page(page("<h1>PS5 Pro</h1><button>Sold Out</button>")).status == OUT_OF_STOCK
    assert analyse_page(page("<h1>PS5 Pro</h1><button disabled>Add to cart</button>")).status == OUT_OF_STOCK


def test_carousel_add_to_cart_is_ignored():
    html = page(
        "<h1>PS5 Pro</h1><button>Coming Soon</button>"
        "<div class='recommendations-carousel'><button>Add to Cart</button></div>"
    )
    assert analyse_page(html).status == OUT_OF_STOCK


def test_meta_tag():
    head = '<meta property="product:availability" content="in stock">'
    assert analyse_page(page("<h1>x</h1>", head)).status == IN_STOCK


def test_no_signal_is_unknown():
    assert analyse_page(page("<h1>PS5 Pro</h1><p>Nice console</p>")).status == UNKNOWN


def test_captcha_detected():
    assert analyse_page("<html><body>Robot or human? Press and hold</body></html>").status == BLOCKED


def test_amazon_style_seller_text():
    html = page("<h1>PlayStation 5 Pro</h1><div>Ships from and sold by Amazon.com.</div>"
                "<input type='submit' value='Add to Cart'>")
    r = analyse_page(html)
    assert r.status == IN_STOCK
    assert r.seller.lower().startswith("amazon")


# --- matching / discovery ---------------------------------------------------

@pytest.mark.parametrize("text", [
    "Sony PlayStation 5 Pro Console - White",
    "PlayStation®5 Pro Console – Call of Duty Bundle",
    "PS5 Pro Digital Console + Extra DualSense Bundle",
    "/products/sony-playstation5-pro-console",
    "/site/sony-interactive-entertainment-playstation-5-pro-console/6596939.p",
])
def test_matches_console(config, text):
    assert config.is_ps5_pro(text)


@pytest.mark.parametrize("text", [
    "PlayStation 5 Slim Console",
    "PS5 Pro Vertical Stand",
    "Hard Carrying Case for PS5 Pro",
    "Skins for PlayStation 5 Pro",
    "PS5 Pro Console - Pre-Owned",
    "Gran Turismo 7 - PS5 Pro Enhanced",
])
def test_rejects_others(config, text):
    assert not config.is_ps5_pro(text)


def test_find_candidates(config):
    html = """
    <a href="/site/sony-playstation-5-pro-console/6596939.p?skuId=6596939&intl=nosplash">PlayStation 5 Pro Console</a>
    <a href="/site/sony-playstation-5-pro-console/6596939.p?skuId=6596939">PlayStation 5 Pro Console</a>
    <a href="/site/ps5-pro-vertical-stand/1111111.p?skuId=1111111">PS5 Pro Vertical Stand</a>
    <a href="/site/playstation-5-slim/2222222.p?skuId=2222222">PlayStation 5 Slim</a>
    <a href="https://evil.example.com/products/ps5-pro">PS5 Pro cheap!!</a>
    <a href="/site/searchpage.jsp?st=ps5+pro">PS5 Pro</a>
    """
    found = find_candidates(html, "https://www.bestbuy.com/site/searchpage.jsp?st=ps5+pro", config, 10)
    assert [u for u, _ in found] == [
        "https://www.bestbuy.com/site/sony-playstation-5-pro-console/6596939.p?skuId=6596939"
    ]


# --- filters + state -----------------------------------------------------------

class FakeFetcher:
    def __init__(self, pages):
        self.pages = pages

    def fetch(self, url, render=False):
        status, html = self.pages[url]
        return Response(url, status, html)


def test_scalper_price_ignored(config):
    r = Retailer("Walmart")
    f = FakeFetcher({"u": (200, page("", jsonld("InStock", price="1469.00")))})
    assert tracker.check_url(f, r, "u", config, discovered=True).status == tracker.IGNORED


def test_marketplace_seller_ignored(config):
    r = Retailer("Walmart", seller_must_match="walmart")
    f = FakeFetcher({"u": (200, page("", jsonld("InStock", seller="GameDealz LLC")))})
    c = tracker.check_url(f, r, "u", config, discovered=True)
    assert c.status == tracker.IGNORED and "third-party" in c.reason


def test_first_party_seller_ok(config):
    r = Retailer("Walmart", seller_must_match="walmart")
    f = FakeFetcher({"u": (200, page("", jsonld("InStock", seller="Walmart.com")))})
    assert tracker.check_url(f, r, "u", config, discovered=True).status == IN_STOCK


def test_404_and_block(config):
    r = Retailer("X")
    f = FakeFetcher({"a": (404, ""), "b": (403, "denied")})
    assert tracker.check_url(f, r, "a", config, False).status == "NOT_FOUND"
    assert tracker.check_url(f, r, "b", config, False).status == BLOCKED


def test_alert_only_on_transition(config, monkeypatch):
    sent = []
    monkeypatch.setattr(tracker.notify, "send", lambda t, b, u=None: sent.append(t) or [])
    state = {}
    c = tracker.Check("Target", "u", IN_STOCK, "PS5 Pro", 899.99)
    assert len(tracker.update_state_and_alert([c], state, config)) == 1
    assert len(tracker.update_state_and_alert([c], state, config)) == 0  # still in stock, no spam
    # A blocked check must not reset state and cause a duplicate alert later.
    tracker.update_state_and_alert([tracker.Check("Target", "u", BLOCKED)], state, config)
    assert len(tracker.update_state_and_alert([c], state, config)) == 0
    tracker.update_state_and_alert([tracker.Check("Target", "u", OUT_OF_STOCK)], state, config)
    assert len(tracker.update_state_and_alert([c], state, config)) == 1
    assert len(sent) == 2


def test_check_retailer_search_flow(config, monkeypatch):
    monkeypatch.setattr(tracker, "polite_sleep", lambda s: None)
    search = "https://www.walmart.com/search?q=ps5+pro"
    product = "https://www.walmart.com/ip/Sony-PlayStation-5-Pro-Console/123456"
    f = FakeFetcher({
        search: (200, page(f'<a href="{product}?athbdg=L1600">Sony PlayStation 5 Pro Console</a>')),
        product: (200, page("", jsonld("InStock", seller="Walmart.com"))),
    })
    r = Retailer("Walmart", search=search, seller_must_match="walmart")
    checks = tracker.check_retailer(f, r, config)
    assert [(c.url, c.status) for c in checks] == [(product, IN_STOCK)]


def test_status_page(config, tmp_path):
    from ps5_pro_tracker import site
    checks = [
        tracker.Check("Target", "https://t/p", IN_STOCK, "PS5 Pro <Console>", 899.99, reason="json-ld"),
        tracker.Check("Walmart", "https://w/p", tracker.IGNORED, "PS5 Pro", 1469.0, reason="scalper"),
        tracker.Check("Amazon", "https://a/s", BLOCKED, reason="HTTP 503"),
    ]
    state = {}
    tracker.update_state_and_alert(checks, state, config, dry_run=True)
    status = site.build_status(checks, state, config)
    assert status["in_stock"] == 1
    assert status["retailers"][0]["name"] == "Target"
    assert status["retailers"][0]["last_in_stock"]
    page = site.write_site(status, tmp_path).read_text()
    assert "<Console>" not in page.split("<script>")[0]  # data only lives inside the script
    assert "</script>" not in page.split("const DATA = ")[1].split(";\nconst LABELS")[0]
    assert (tmp_path / "status.json").exists()


@pytest.mark.parametrize("text", [
    "nacon revolution 5 pro wireless controller for playstation 5 /products/nacon-revolution-5-pro",
    "Sony Disc Drive for PlayStation 5 Pro and Digital Edition Consoles",
    "HyperX Cloud headset for PS5 Pro",
    "(Open Box) Sony PlayStation 5 Pro Console",
])
def test_rejects_accessories(config, text):
    assert not config.is_ps5_pro(text)


@pytest.mark.parametrize("text", [
    "PlayStation 5 Pro Console + 30th Anniversary Limited Edition DualSense",
    "PlayStation 5 Pro Console + Disc Drive For PS5 Digital Edition Console",
    "PS5 Pro DualSense Controller Bundle",
])
def test_keeps_console_bundles(config, text):
    assert config.is_ps5_pro(text)


def test_seller_from_embedded_json(config):
    html = page('<button>Add to cart</button><script id="__NEXT_DATA__">{"sellerName":"BigDeals Store"}</script>')
    f = FakeFetcher({"u": (200, html)})
    c = tracker.check_url(f, Retailer("Walmart", seller_must_match="walmart"), "u", config, discovered=False)
    assert c.status == tracker.IGNORED and "BigDeals" in c.reason


def test_unconfirmed_seller_is_flagged(config):
    f = FakeFetcher({"u": (200, page("<h1>PlayStation 5 Pro Console</h1><button>Add to cart</button>"))})
    c = tracker.check_url(f, Retailer("Walmart", seller_must_match="walmart"), "u", config, discovered=True)
    assert c.status == IN_STOCK and "seller not confirmed" in c.reason
