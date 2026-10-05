"""Load and validate config.yaml."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
IATA_RE = re.compile(r"^[A-Z]{3}$")


class ConfigError(ValueError):
    pass


@dataclass
class Search:
    name: str
    origin: str
    destination: str
    months: list[str]
    max_price: float
    trip: str = "one-way"  # "one-way" or "return"
    min_nights: int | None = None
    max_nights: int | None = None
    direct_only: bool = False
    adults: int = 1

    @property
    def is_return(self) -> bool:
        return self.trip == "return"


@dataclass
class Verification:
    enabled: bool = True
    max_checks_per_search: int = 8
    delay_seconds: float = 3.0
    # What to do when Google Flights can't be reached or returns nothing usable:
    # "send" (alert anyway, marked unverified) or "skip".
    on_error: str = "send"


@dataclass
class Notify:
    server: str = "https://ntfy.sh"
    max_deals_per_message: int = 5
    # Re-alert for an already-notified flight only if it got this much cheaper.
    renotify_drop_pct: float = 10.0
    # Use high priority when the cheapest deal is at or below this share of max_price.
    high_priority_ratio: float = 0.75


@dataclass
class Config:
    currency: str
    markets: list[str]
    skyscanner_domain: str
    searches: list[Search]
    verification: Verification = field(default_factory=Verification)
    notify: Notify = field(default_factory=Notify)


def _require(d: dict, key: str, where: str):
    if key not in d or d[key] in (None, ""):
        raise ConfigError(f"{where}: missing '{key}'")
    return d[key]


def _parse_search(raw: dict, idx: int) -> Search:
    where = f"searches[{idx}]"
    origin = str(_require(raw, "origin", where)).upper()
    destination = str(_require(raw, "destination", where)).upper()
    for label, code in (("origin", origin), ("destination", destination)):
        if not IATA_RE.match(code):
            raise ConfigError(f"{where}: {label} must be a 3-letter IATA code, got {code!r}")

    months = raw.get("months") or ([raw["month"]] if raw.get("month") else None)
    if not months:
        raise ConfigError(f"{where}: set 'month' or 'months' (YYYY-MM)")
    months = [str(m) for m in months]
    for m in months:
        if not MONTH_RE.match(m):
            raise ConfigError(f"{where}: month must look like 2026-11, got {m!r}")

    trip = str(raw.get("trip", "one-way")).lower()
    if trip not in ("one-way", "return"):
        raise ConfigError(f"{where}: trip must be 'one-way' or 'return'")

    min_nights = max_nights = None
    if trip == "return":
        nights = raw.get("nights")
        if nights is None:
            raise ConfigError(f"{where}: return trips need 'nights', e.g. [3, 7] or 5")
        if isinstance(nights, int):
            min_nights = max_nights = nights
        elif isinstance(nights, list) and len(nights) == 2:
            min_nights, max_nights = int(nights[0]), int(nights[1])
        else:
            raise ConfigError(f"{where}: 'nights' must be a number or [min, max]")
        if min_nights < 0 or max_nights < min_nights:
            raise ConfigError(f"{where}: invalid nights range {nights!r}")

    max_price = float(_require(raw, "max_price", where))
    if max_price <= 0:
        raise ConfigError(f"{where}: max_price must be positive")

    adults = int(raw.get("adults", 1))
    if not 1 <= adults <= 9:
        raise ConfigError(f"{where}: adults must be between 1 and 9")

    return Search(
        name=str(raw.get("name") or f"{origin}→{destination}"),
        origin=origin,
        destination=destination,
        months=months,
        max_price=max_price,
        trip=trip,
        min_nights=min_nights,
        max_nights=max_nights,
        direct_only=bool(raw.get("direct_only", False)),
        adults=adults,
    )


def _parse_markets(raw: dict) -> list[str]:
    markets = raw.get("markets") or raw.get("market") or "us"
    if isinstance(markets, str):
        markets = [markets]
    return [str(m).lower() for m in markets]


def load_config(path: str | Path) -> Config:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    searches_raw = raw.get("searches") or []
    if not searches_raw:
        raise ConfigError("config: add at least one entry under 'searches'")

    verification = Verification(**(raw.get("verification") or {}))
    if verification.on_error not in ("send", "skip"):
        raise ConfigError("verification.on_error must be 'send' or 'skip'")

    return Config(
        currency=str(raw.get("currency", "USD")).upper(),
        markets=_parse_markets(raw),
        skyscanner_domain=str(raw.get("skyscanner_domain", "www.skyscanner.net")),
        searches=[_parse_search(s, i) for i, s in enumerate(searches_raw)],
        verification=verification,
        notify=Notify(**(raw.get("notify") or {})),
    )
