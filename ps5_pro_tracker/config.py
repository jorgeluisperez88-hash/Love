"""Load ``retailers.yaml`` and decide which listings count as a PS5 Pro."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Retailer:
    name: str
    products: list[str] = field(default_factory=list)
    search: str | None = None
    render: bool = False
    seller_must_match: str | None = None
    use_bestbuy_api: bool = False
    enabled: bool = True


@dataclass
class Settings:
    max_price: float = 1050
    min_price: float = 500
    max_candidates_per_search: int = 6
    per_request_delay: float = 3
    realert_after_minutes: int = 60
    request_timeout: float = 25


@dataclass
class Config:
    settings: Settings
    retailers: list[Retailer]
    include: list[re.Pattern[str]]
    exclude: list[re.Pattern[str]]

    def is_ps5_pro(self, text: str | None) -> bool:
        """Does this title / link text / URL describe a PS5 Pro console or bundle?"""
        if not text:
            return False
        t = normalise(text)
        return any(p.search(t) for p in self.include) and not any(p.search(t) for p in self.exclude)

    def price_ok(self, price: float | None) -> bool:
        if price is None:
            return True  # unknown price: don't throw away a possible restock
        return self.settings.min_price <= price <= self.settings.max_price


def normalise(text: str) -> str:
    """Lower-case and turn URL slugs into words ("ps5-pro" -> "ps5 pro")."""
    t = text.lower().replace("®", "").replace("™", "")
    t = re.sub(r"%20|\+|[-_/]", " ", t)
    return re.sub(r"\s+", " ", t)


def load_config(path: str | Path) -> Config:
    raw: dict[str, Any] = yaml.safe_load(Path(path).read_text()) or {}
    s = raw.get("settings") or {}
    settings = Settings(**{k: v for k, v in s.items() if k in Settings.__dataclass_fields__})
    retailers = []
    for r in raw.get("retailers") or []:
        retailers.append(
            Retailer(
                name=r["name"],
                products=list(r.get("products") or []),
                search=r.get("search"),
                render=bool(r.get("render", False)),
                seller_must_match=r.get("seller_must_match"),
                use_bestbuy_api=bool(r.get("use_bestbuy_api", False)),
                enabled=bool(r.get("enabled", True)),
            )
        )
    match = raw.get("match") or {}
    include = [re.compile(p, re.I) for p in match.get("include", [])]
    exclude = [re.compile(p, re.I) for p in match.get("exclude", [])]
    return Config(settings, retailers, include, exclude)
