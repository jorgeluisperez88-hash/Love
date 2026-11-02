"""Check every configured retailer once, alert on restocks, record state."""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import bestbuy_api, notify, site
from .config import Config, Retailer, load_config
from .detect import BLOCKED, ERROR, IN_STOCK, NOT_FOUND, OUT_OF_STOCK, UNKNOWN, analyse_page, looks_blocked
from .discover import find_candidates
from .fetch import Fetcher, polite_sleep

log = logging.getLogger("ps5_pro_tracker")

IGNORED = "IGNORED"  # in stock, but filtered out (scalper price / marketplace seller)


@dataclass
class Check:
    retailer: str
    url: str
    status: str
    title: str | None = None
    price: float | None = None
    seller: str | None = None
    reason: str = ""


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# checking
# ---------------------------------------------------------------------------

def _apply_filters(check: Check, retailer: Retailer, config: Config, discovered: bool) -> Check:
    if check.status != IN_STOCK:
        return check
    if discovered and check.title and not config.is_ps5_pro(check.title):
        check.status, check.reason = IGNORED, f"not a PS5 Pro console/bundle: {check.title!r}"
    elif not config.price_ok(check.price):
        check.status, check.reason = IGNORED, (
            f"price ${check.price:,.2f} outside ${config.settings.min_price:,.0f}-"
            f"${config.settings.max_price:,.0f} (likely scalper or accessory)"
        )
    elif retailer.seller_must_match and check.seller and not re.search(
        retailer.seller_must_match, check.seller, re.I
    ):
        check.status, check.reason = IGNORED, f"third-party marketplace seller: {check.seller}"
    return check


def check_url(fetcher: Fetcher, retailer: Retailer, url: str, config: Config, discovered: bool) -> Check:
    try:
        resp = fetcher.fetch(url, render=retailer.render)
    except Exception as exc:
        return Check(retailer.name, url, ERROR, reason=f"{type(exc).__name__}: {exc}"[:200])
    if resp.status in (404, 410):
        return Check(retailer.name, url, NOT_FOUND, reason=f"HTTP {resp.status} - listing removed?")
    if resp.status in (403, 429, 503) or looks_blocked(resp.text):
        return Check(retailer.name, url, BLOCKED, reason=f"HTTP {resp.status} - bot protection")
    if resp.status >= 400:
        return Check(retailer.name, url, ERROR, reason=f"HTTP {resp.status}")
    page = analyse_page(resp.text)
    check = Check(retailer.name, url, page.status, page.title, page.price, page.seller, page.reason)
    return _apply_filters(check, retailer, config, discovered)


def check_retailer(fetcher: Fetcher, retailer: Retailer, config: Config) -> list[Check]:
    s = config.settings

    api_key = os.environ.get("BESTBUY_API_KEY", "").strip()
    if retailer.use_bestbuy_api and api_key:
        try:
            items = bestbuy_api.search(api_key, config, timeout=s.request_timeout)
            checks = [Check(retailer.name, **item) for item in items]
            if not checks:
                return [Check(retailer.name, "https://api.bestbuy.com", OUT_OF_STOCK,
                              reason="Best Buy API: no PS5 Pro listings returned")]
            return [_apply_filters(c, retailer, config, discovered=True) for c in checks]
        except Exception as exc:
            log.warning("Best Buy API failed (%s); falling back to the website", exc)

    targets: list[tuple[str, bool]] = [(u, False) for u in retailer.products]
    checks: list[Check] = []

    if retailer.search:
        try:
            resp = fetcher.fetch(retailer.search, render=retailer.render)
            if resp.status in (403, 429, 503) or looks_blocked(resp.text):
                checks.append(Check(retailer.name, retailer.search, BLOCKED,
                                    reason=f"search page blocked (HTTP {resp.status})"))
            elif resp.status >= 400:
                checks.append(Check(retailer.name, retailer.search, ERROR,
                                    reason=f"search page HTTP {resp.status}"))
            else:
                found = find_candidates(resp.text, resp.url or retailer.search, config,
                                        s.max_candidates_per_search)
                known = {u for u, _ in targets}
                targets += [(u, True) for u, _ in found if u not in known]
                if not found and not retailer.products:
                    checks.append(Check(retailer.name, retailer.search, OUT_OF_STOCK,
                                        reason="no PS5 Pro listings on search page"))
        except Exception as exc:
            checks.append(Check(retailer.name, retailer.search, ERROR,
                                reason=f"search failed: {type(exc).__name__}: {exc}"[:200]))
        polite_sleep(s.per_request_delay)

    for i, (url, discovered) in enumerate(targets):
        if i:
            polite_sleep(s.per_request_delay)
        checks.append(check_url(fetcher, retailer, url, config, discovered))
    return checks


# ---------------------------------------------------------------------------
# state + alerting
# ---------------------------------------------------------------------------

def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True))


def update_state_and_alert(checks: list[Check], state: dict, config: Config, dry_run: bool = False) -> list[Check]:
    """Record results; alert on new restocks. Returns the checks that alerted."""
    now = _now()
    realert = timedelta(minutes=config.settings.realert_after_minutes)
    alerted: list[Check] = []
    for c in checks:
        prev = state.get(c.url, {})
        entry = {
            **prev,
            "retailer": c.retailer,
            "status": c.status,
            "title": c.title or prev.get("title"),
            "price": c.price if c.price is not None else prev.get("price"),
            "reason": c.reason,
            "last_checked": now.isoformat(),
        }
        # Transient failures shouldn't erase what we last knew.
        if c.status in (BLOCKED, ERROR, UNKNOWN):
            entry["status"] = prev.get("status", c.status)
            entry["last_problem"] = f"{c.status}: {c.reason}"
        elif prev.get("status") != c.status:
            entry["last_change"] = now.isoformat()

        if c.status == IN_STOCK:
            entry["last_in_stock"] = now.isoformat()
            last_alert = prev.get("last_alert")
            was_in_stock = prev.get("status") == IN_STOCK
            due = not last_alert or now - datetime.fromisoformat(last_alert) >= realert
            if not was_in_stock or due:
                alerted.append(c)
                if not dry_run:
                    entry["last_alert"] = now.isoformat()
        state[c.url] = entry

    if alerted and not dry_run:
        for c in alerted:
            price = f" - ${c.price:,.2f}" if c.price else ""
            title = f"PS5 Pro IN STOCK at {c.retailer}{price}"
            body = f"{c.title or 'PS5 Pro'}\n{c.url}\n({c.reason})"
            failed = notify.send(title, body, c.url)
            if failed:
                log.error("some alert channels failed: %s", ", ".join(failed))
    return alerted


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------

_ICON = {IN_STOCK: "🟢", OUT_OF_STOCK: "🔴", IGNORED: "⚪", BLOCKED: "🟠", ERROR: "🟠",
         NOT_FOUND: "⚫", UNKNOWN: "❔"}


def report(checks: list[Check], alerted: list[Check]) -> str:
    lines = [
        f"## PS5 Pro stock check - {_now():%Y-%m-%d %H:%M UTC}",
        "",
        f"**{sum(c.status == IN_STOCK for c in checks)} in stock** · "
        f"{len(alerted)} new alert(s) · {len(checks)} listings checked",
        "",
        "| | Retailer | Status | Item | Price | Notes |",
        "|---|---|---|---|---|---|",
    ]
    order = {IN_STOCK: 0, IGNORED: 1, OUT_OF_STOCK: 2, UNKNOWN: 3, BLOCKED: 4, ERROR: 5, NOT_FOUND: 6}
    for c in sorted(checks, key=lambda c: (order.get(c.status, 9), c.retailer)):
        item = (c.title or "—").replace("|", "/")[:70]
        price = f"${c.price:,.2f}" if c.price else "—"
        lines.append(
            f"| {_ICON.get(c.status, '')} | {c.retailer} | {c.status} | [{item}]({c.url}) | {price} | "
            f"{c.reason.replace('|', '/')[:90]} |"
        )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def run_once(config: Config, state_path: Path, only: list[str] | None, no_browser: bool, dry_run: bool,
             site_dir: str | None = "site") -> list[Check]:
    fetcher = Fetcher(timeout=config.settings.request_timeout, allow_browser=not no_browser)
    checks: list[Check] = []
    try:
        retailers = [r for r in config.retailers if r.enabled]
        if only:
            wanted = {o.lower() for o in only}
            retailers = [r for r in config.retailers if r.name.lower() in wanted]
        random.shuffle(retailers)  # don't hit sites in the same order every time
        for r in retailers:
            log.info("checking %s", r.name)
            try:
                results = check_retailer(fetcher, r, config)
            except Exception as exc:  # never let one site kill the run
                log.exception("%s failed", r.name)
                results = [Check(r.name, r.search or "", ERROR, reason=str(exc)[:200])]
            for c in results:
                log.info("  %-12s %s %s", c.status, c.url, f"({c.reason})" if c.reason else "")
            checks.extend(results)
    finally:
        fetcher.close()

    state = load_state(state_path)
    alerted = update_state_and_alert(checks, state, config, dry_run=dry_run)
    save_state(state_path, state)
    if site_dir:
        page = site.write_site(site.build_status(checks, state, config), site_dir)
        log.info("status page written to %s", page)

    text = report(checks, alerted)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as fh:
            fh.write(text)
    return checks


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ps5-pro-tracker", description="Watch retailers for PS5 Pro restocks.")
    ap.add_argument("-c", "--config", default="retailers.yaml")
    ap.add_argument("-s", "--state", default="state/state.json")
    ap.add_argument("--loop", action="store_true", help="keep running, checking every --interval seconds")
    ap.add_argument("--interval", type=int, default=600, help="seconds between runs in --loop mode (default 600)")
    ap.add_argument("--only", nargs="+", metavar="RETAILER", help="only check these retailers (by name)")
    ap.add_argument("--no-browser", action="store_true", help="never launch the headless browser")
    ap.add_argument("--dry-run", action="store_true", help="don't send alerts")
    ap.add_argument("--site-dir", default="site", help="where to write the status web page ('' to skip)")
    ap.add_argument("--test-alert", action="store_true", help="send a test notification and exit")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    if args.test_alert:
        channels = notify.configured_channels()
        if not channels:
            print("No alert channels configured. See README.md -> Alerts.", file=sys.stderr)
            return 1
        failed = notify.send("PS5 Pro tracker test alert", "If you can read this, alerts work.",
                             "https://direct.playstation.com/en-us/buy-consoles/playstation5-pro-console")
        print(f"sent via: {', '.join(c for c in channels if c not in failed) or 'none'}")
        return 1 if failed else 0

    config = load_config(args.config)
    if not notify.configured_channels() and not args.dry_run:
        log.warning("no alert channels configured - restocks will only be printed")

    state_path = Path(args.state)
    while True:
        run_once(config, state_path, args.only, args.no_browser, args.dry_run, args.site_dir or None)
        if not args.loop:
            return 0
        wait = args.interval + random.randint(0, max(1, args.interval // 5))
        log.info("next check in %ds", wait)
        time.sleep(wait)


if __name__ == "__main__":
    sys.exit(main())
