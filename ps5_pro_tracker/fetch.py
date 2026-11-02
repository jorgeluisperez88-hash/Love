"""Download pages, either with plain HTTP or a headless browser.

Plain HTTP uses ``curl_cffi`` when it's installed, because it presents a real
Chrome TLS fingerprint and is blocked far less often than ``requests``.
Pages marked ``render: true`` are loaded in Playwright Chromium so that
JavaScript-built product data is present.
"""

from __future__ import annotations

import logging
import os
import random
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/141.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/141.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/18.5 Safari/605.1.15",
]

BASE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Upgrade-Insecure-Requests": "1",
}


@dataclass
class Response:
    url: str
    status: int
    text: str


class Fetcher:
    def __init__(self, timeout: float = 25, allow_browser: bool = True):
        self.timeout = timeout
        self.allow_browser = allow_browser
        self._session = None
        self._impersonate = False
        self._pw = None
        self._browser = None
        self._context = None

    # -- plain HTTP ---------------------------------------------------------
    def _http_session(self):
        if self._session is None:
            try:
                from curl_cffi import requests as creq  # type: ignore

                self._session = creq.Session()
                self._impersonate = True
            except ImportError:
                import requests

                self._session = requests.Session()
        return self._session

    def get(self, url: str) -> Response:
        session = self._http_session()
        headers = dict(BASE_HEADERS)
        kwargs = {"timeout": self.timeout, "allow_redirects": True}
        if self._impersonate:
            kwargs["impersonate"] = "chrome"
        else:
            headers["User-Agent"] = random.choice(USER_AGENTS)
        r = session.get(url, headers=headers, **kwargs)
        return Response(str(r.url), r.status_code, r.text)

    # -- headless browser ---------------------------------------------------
    def _browser_context(self):
        if self._context is None:
            from playwright.sync_api import sync_playwright  # type: ignore

            self._pw = sync_playwright().start()
            launch = {"headless": True, "args": ["--disable-blink-features=AutomationControlled"]}
            if os.environ.get("CHROMIUM_PATH"):
                launch["executable_path"] = os.environ["CHROMIUM_PATH"]
            self._browser = self._pw.chromium.launch(**launch)
            self._context = self._browser.new_context(
                user_agent=USER_AGENTS[0],
                locale="en-US",
                viewport={"width": 1366, "height": 900},
            )
        return self._context

    def render(self, url: str, settle_ms: int = 3500) -> Response:
        if not self.allow_browser:
            return self.get(url)
        try:
            ctx = self._browser_context()
        except Exception as exc:  # Playwright not installed / no browser
            log.warning("headless browser unavailable (%s); falling back to plain HTTP", exc)
            self.allow_browser = False
            return self.get(url)
        page = ctx.new_page()
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
            try:
                page.wait_for_load_state("networkidle", timeout=settle_ms * 2)
            except Exception:
                pass
            page.wait_for_timeout(settle_ms)
            return Response(page.url, resp.status if resp else 0, page.content())
        finally:
            page.close()

    def fetch(self, url: str, render: bool = False) -> Response:
        return self.render(url) if render else self.get(url)

    def close(self) -> None:
        for obj, meth in ((self._context, "close"), (self._browser, "close"), (self._pw, "stop")):
            if obj is not None:
                try:
                    getattr(obj, meth)()
                except Exception:
                    pass
        self._context = self._browser = self._pw = None


def polite_sleep(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds + random.uniform(0, seconds / 2))
