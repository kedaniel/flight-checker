"""Build and send ntfy notifications."""

from __future__ import annotations

from datetime import date

import requests

from .config import Config, Search
from .models import Deal

SYMBOLS = {"GBP": "£", "USD": "$", "EUR": "€", "JPY": "¥", "INR": "₹", "AUD": "A$", "CAD": "C$"}
MAX_ACTIONS = 3  # ntfy allows at most 3 action buttons


def money(amount: float, currency: str) -> str:
    value = f"{amount:,.0f}"
    sym = SYMBOLS.get(currency)
    return f"{sym}{value}" if sym else f"{value} {currency}"


def short_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d:%a} {d.day} {d:%b}"


def skyscanner_url(deal: Deal, search: Search, domain: str) -> str:
    def fmt(iso: str) -> str:
        return date.fromisoformat(iso).strftime("%y%m%d")

    path = f"{deal.origin.lower()}/{deal.destination.lower()}/{fmt(deal.depart_date)}/"
    if deal.return_date:
        path += f"{fmt(deal.return_date)}/"
    params = f"adultsv2={search.adults}&cabinclass=economy&rtn={1 if deal.return_date else 0}"
    if search.direct_only:
        params += "&preferdirects=true"
    return f"https://{domain}/transport/flights/{path}?{params}"


def deal_line(deal: Deal, currency: str) -> str:
    when = short_date(deal.depart_date)
    if deal.return_date:
        nights = (date.fromisoformat(deal.return_date) - date.fromisoformat(deal.depart_date)).days
        when += f" → {short_date(deal.return_date)} ({nights}n)"
    stops = "direct" if deal.transfers == 0 else f"{deal.transfers} stop{'s' if deal.transfers > 1 else ''}"
    parts = [money(deal.best_price, currency), when, f"{deal.origin}→{deal.destination}"]
    if deal.airline:
        parts.append(deal.airline)
    parts.append(stops)
    parts.append("✓ Google" if deal.verified else "unverified")
    return " · ".join(parts)


def build_message(deals: list[Deal], search: Search, cfg: Config) -> dict:
    """ntfy JSON payload (topic is added when sending)."""
    deals = sorted(deals, key=lambda d: (d.best_price, d.depart_date))
    shown = deals[: cfg.notify.max_deals_per_message]
    cheapest = deals[0]
    n = len(deals)
    title = (
        f"✈ {search.name}: {n} flight{'s' if n > 1 else ''} under "
        f"{money(search.max_price, cfg.currency)}"
    )
    lines = [deal_line(d, cfg.currency) for d in shown]
    if n > len(shown):
        lines.append(f"…and {n - len(shown)} more")
    if search.adults > 1:
        lines.append(f"Prices are per person ({search.adults} adults).")

    high = cheapest.best_price <= search.max_price * cfg.notify.high_priority_ratio
    return {
        "title": title,
        "message": "\n".join(lines),
        "priority": 4 if high else 3,
        "tags": ["airplane"] + (["fire"] if high else []),
        "click": skyscanner_url(cheapest, search, cfg.skyscanner_domain),
        "actions": [
            {
                "action": "view",
                "label": f"{money(d.best_price, cfg.currency)} {short_date(d.depart_date)}",
                "url": skyscanner_url(d, search, cfg.skyscanner_domain),
                "clear": False,
            }
            for d in shown[:MAX_ACTIONS]
        ],
    }


def send(payload: dict, server: str, topic: str, token: str | None = None) -> None:
    # Publishing as JSON (rather than headers) keeps non-ASCII titles like "£" and "→" intact.
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    resp = requests.post(
        server.rstrip("/") + "/", json={"topic": topic, **payload}, headers=headers, timeout=30
    )
    resp.raise_for_status()
