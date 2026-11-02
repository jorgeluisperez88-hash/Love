"""Build a static status page (``site/index.html`` + ``site/status.json``).

The page is self-contained (no external scripts) so it can be served from
GitHub Pages or opened straight from disk. The data is embedded in the page
and also written to ``status.json``.
"""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Config
    from .tracker import Check

STATUS_LABEL = {
    "IN_STOCK": "In stock",
    "OUT_OF_STOCK": "Sold out",
    "IGNORED": "Skipped",
    "BLOCKED": "Couldn't check",
    "ERROR": "Couldn't check",
    "UNKNOWN": "Unclear",
    "NOT_FOUND": "Link gone",
}

# Retailer-level verdict, best first.
_RANK = ["IN_STOCK", "OUT_OF_STOCK", "IGNORED", "UNKNOWN", "BLOCKED", "ERROR", "NOT_FOUND"]


def build_status(checks: list["Check"], state: dict, config: "Config", now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    by_retailer: dict[str, list[dict]] = {}
    for c in checks:
        st = state.get(c.url, {})
        by_retailer.setdefault(c.retailer, []).append(
            {
                "url": c.url,
                "title": c.title or st.get("title"),
                "price": c.price if c.price is not None else st.get("price"),
                "status": c.status,
                "reason": c.reason,
                "last_in_stock": st.get("last_in_stock"),
                "last_change": st.get("last_change"),
            }
        )

    retailers = []
    for r in config.retailers:
        if not r.enabled and r.name not in by_retailer:
            continue
        items = by_retailer.get(r.name, [])
        statuses = [i["status"] for i in items]
        overall = next((s for s in _RANK if s in statuses), "NOT_CHECKED")
        # A blocked/broken retailer whose other listings were read fine is "sold out".
        items.sort(key=lambda i: _RANK.index(i["status"]) if i["status"] in _RANK else 99)
        last_in_stock = max((i["last_in_stock"] for i in items if i["last_in_stock"]), default=None)
        retailers.append(
            {
                "name": r.name,
                "status": overall,
                "home": r.search or (r.products[0] if r.products else None),
                "last_in_stock": last_in_stock,
                "items": items,
            }
        )
    retailers.sort(key=lambda x: (_RANK.index(x["status"]) if x["status"] in _RANK else 99, x["name"]))
    return {
        "generated_at": now.isoformat(),
        "in_stock": sum(1 for c in checks if c.status == "IN_STOCK"),
        "max_price": config.settings.max_price,
        "retailers": retailers,
    }


def write_site(status: dict, out_dir: str | Path) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "status.json").write_text(json.dumps(status, indent=2))
    data = json.dumps(status).replace("</", "<\\/")
    page = _TEMPLATE.replace("__DATA__", data).replace(
        "__LABELS__", json.dumps(STATUS_LABEL)
    ).replace("__UPDATED__", html.escape(status["generated_at"]))
    (out / "index.html").write_text(page)
    return out / "index.html"


_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="300">
<title>PS5 Pro Stock</title>
<style>
:root {
  --bg: #f4f5f7; --card: #ffffff; --text: #16181d; --muted: #5d6470; --line: #e3e6ea;
  --accent: #0070d1;
  --in-bg: #e3f6ea; --in-fg: #0f7a3d; --out-bg: #fbe9ea; --out-fg: #b42330;
  --warn-bg: #fff3dc; --warn-fg: #8a5a00; --neutral-bg: #eef0f3; --neutral-fg: #4b525d;
  color-scheme: light;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #0f1115; --card: #181b21; --text: #eceef2; --muted: #9aa2ae; --line: #2a2f37;
    --accent: #4da3ff;
    --in-bg: #12321f; --in-fg: #5fd68f; --out-bg: #3a171b; --out-fg: #ff8a93;
    --warn-bg: #36290d; --warn-fg: #f2c063; --neutral-bg: #252a32; --neutral-fg: #b7bec9;
    color-scheme: dark;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 15px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
main { max-width: 760px; margin: 0 auto; padding: 24px 16px 48px; }
header h1 { font-size: 22px; margin: 0 0 4px; letter-spacing: -0.01em; }
header p { margin: 0; color: var(--muted); font-size: 13px; }
.hero { margin: 20px 0 24px; padding: 20px; border-radius: 14px; background: var(--card);
  border: 1px solid var(--line); display: flex; gap: 14px; align-items: center; }
.hero .dot { width: 14px; height: 14px; border-radius: 50%; flex: none; }
.hero.in { background: var(--in-bg); border-color: transparent; }
.hero.in .dot { background: var(--in-fg); box-shadow: 0 0 0 6px color-mix(in srgb, var(--in-fg) 25%, transparent); }
.hero.out .dot { background: var(--out-fg); }
.hero h2 { margin: 0; font-size: 19px; }
.hero.in h2 { color: var(--in-fg); }
.hero p { margin: 2px 0 0; color: var(--muted); font-size: 13px; }
.stale { color: var(--warn-fg); font-weight: 600; }
.store { background: var(--card); border: 1px solid var(--line); border-radius: 12px;
  margin-bottom: 10px; overflow: hidden; }
.store > summary { list-style: none; cursor: pointer; padding: 14px 16px; display: flex;
  align-items: center; gap: 12px; }
.store > summary::-webkit-details-marker { display: none; }
.store .name { font-weight: 600; flex: 1; min-width: 0; }
.store .sub { display: block; font-weight: 400; color: var(--muted); font-size: 12.5px; }
.chev { color: var(--muted); transition: transform .15s; font-size: 12px; }
.store[open] .chev { transform: rotate(90deg); }
.pill { font-size: 12px; font-weight: 600; padding: 3px 10px; border-radius: 999px; white-space: nowrap; }
.s-IN_STOCK { background: var(--in-bg); color: var(--in-fg); }
.s-OUT_OF_STOCK { background: var(--out-bg); color: var(--out-fg); }
.s-BLOCKED, .s-ERROR, .s-UNKNOWN { background: var(--warn-bg); color: var(--warn-fg); }
.s-IGNORED, .s-NOT_FOUND, .s-NOT_CHECKED { background: var(--neutral-bg); color: var(--neutral-fg); }
.items { border-top: 1px solid var(--line); }
.item { padding: 12px 16px; display: grid; grid-template-columns: 1fr auto; gap: 4px 12px; }
.item + .item { border-top: 1px solid var(--line); }
.item a { color: var(--accent); text-decoration: none; overflow-wrap: anywhere; }
.item a:hover { text-decoration: underline; }
.item .price { font-variant-numeric: tabular-nums; font-weight: 600; text-align: right; }
.item .note { grid-column: 1 / -1; color: var(--muted); font-size: 12.5px; }
.buy { display: inline-block; margin-top: 6px; background: var(--in-fg); color: var(--card) !important;
  padding: 6px 12px; border-radius: 8px; font-weight: 600; font-size: 13px; }
.legend { margin-top: 28px; color: var(--muted); font-size: 12.5px; display: flex; flex-wrap: wrap; gap: 8px 14px; }
.legend span { display: inline-flex; gap: 6px; align-items: center; }
footer { margin-top: 18px; color: var(--muted); font-size: 12px; }
</style>
</head>
<body>
<main>
  <header>
    <h1>PS5 Pro Stock</h1>
    <p>PlayStation 5 Pro consoles &amp; bundles at Sony and authorized US retailers. Refreshes every 5 minutes.</p>
  </header>
  <section id="hero" class="hero"></section>
  <section id="stores"></section>
  <div class="legend">
    <span><b class="pill s-IN_STOCK">In stock</b> can be bought now</span>
    <span><b class="pill s-OUT_OF_STOCK">Sold out</b></span>
    <span><b class="pill s-IGNORED">Skipped</b> scalper price or third-party seller</span>
    <span><b class="pill s-BLOCKED">Couldn't check</b> store blocked the check</span>
  </div>
  <footer>Last check: <time id="updated" datetime="__UPDATED__">__UPDATED__</time></footer>
</main>
<script>
const DATA = __DATA__;
const LABELS = __LABELS__;
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const money = p => p == null ? "" : "$" + Number(p).toLocaleString("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 2});
function ago(iso) {
  if (!iso) return "";
  const s = Math.max(0, (Date.now() - new Date(iso)) / 1000);
  if (s < 90) return "just now";
  if (s < 3600) return Math.round(s / 60) + " min ago";
  if (s < 86400 * 2) return Math.round(s / 3600) + " h ago";
  return new Date(iso).toLocaleDateString(undefined, {month: "short", day: "numeric"});
}
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
const label = s => LABELS[s] || (s === "NOT_CHECKED" ? "Not checked" : s);

const staleMin = (Date.now() - new Date(DATA.generated_at)) / 60000;
const hero = document.getElementById("hero");
const inStock = DATA.retailers.flatMap(r => r.items.filter(i => i.status === "IN_STOCK").map(i => ({...i, store: r.name})));
const staleNote = staleMin > 40 ? ` · <span class="stale">no check for ${ago(DATA.generated_at)} – the tracker may be paused</span>` : "";
if (inStock.length) {
  hero.className = "hero in";
  hero.innerHTML = `<span class="dot"></span><div><h2>In stock at ${esc([...new Set(inStock.map(i => i.store))].join(", "))}</h2>
    <p>Checked ${ago(DATA.generated_at)}${staleNote}</p></div>`;
} else {
  hero.className = "hero out";
  const seen = DATA.retailers.map(r => r.last_in_stock).filter(Boolean).sort().pop();
  hero.innerHTML = `<span class="dot"></span><div><h2>Sold out everywhere</h2>
    <p>Checked ${ago(DATA.generated_at)}${seen ? " · last seen in stock " + ago(seen) : ""}${staleNote}</p></div>`;
}

document.getElementById("stores").innerHTML = DATA.retailers.map(r => {
  const listings = r.items.filter(i => i.title || i.status === "IN_STOCK");
  const sub = r.status === "NOT_CHECKED" ? "Not checked yet"
    : r.status === "IN_STOCK"
    ? `${plural(r.items.filter(i => i.status === "IN_STOCK").length, "listing")} available`
    : (r.last_in_stock ? `Last in stock ${ago(r.last_in_stock)}` : (listings.length ? `${plural(listings.length, "listing")} found` : "No PS5 Pro listings found"));
  const items = r.items.map(i => `
    <div class="item">
      <div><a href="${esc(i.url)}" target="_blank" rel="noopener">${esc(i.title || (i.url.includes("search") || i.url.includes("/s?") ? "Search results" : "Product page"))}</a>
        ${i.status === "IN_STOCK" ? `<br><a class="buy" href="${esc(i.url)}" target="_blank" rel="noopener">Go to store →</a>` : ""}</div>
      <div><span class="pill s-${esc(i.status)}">${esc(label(i.status))}</span>
        <div class="price">${money(i.price)}</div></div>
      ${i.reason ? `<div class="note">${esc(i.reason)}</div>` : ""}
    </div>`).join("");
  return `<details class="store"${r.status === "IN_STOCK" ? " open" : ""}>
    <summary><span class="chev">▶</span>
      <span class="name">${esc(r.name)}<span class="sub">${esc(sub)}</span></span>
      <span class="pill s-${esc(r.status)}">${esc(label(r.status))}</span></summary>
    <div class="items">${items || `<div class="item"><div class="note">Not checked this run.</div></div>`}</div>
  </details>`;
}).join("");

const t = document.getElementById("updated");
t.textContent = new Date(DATA.generated_at).toLocaleString() + " (" + ago(DATA.generated_at) + ")";
</script>
</body>
</html>
"""
