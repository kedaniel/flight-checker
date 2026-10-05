"""Find cheap flights for a whole month with the Travelpayouts (Aviasales) Data API.

Prices are cached from recent user searches, so they can be a little stale;
that's why matches are re-checked against Google Flights before alerting.
Docs: https://support.travelpayouts.com/hc/en-us/articles/203956163
"""

from __future__ import annotations

from datetime import date

import requests

from .config import Config, Search
from .models import Deal

API_URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"


class TravelpayoutsError(RuntimeError):
    pass


def fetch_month(
    search: Search, month: str, cfg: Config, token: str, session: requests.Session | None = None
) -> list[dict]:
    params = {
        "origin": search.origin,
        "destination": search.destination,
        "departure_at": month,
        "one_way": "false" if search.is_return else "true",
        "direct": "true" if search.direct_only else "false",
        "currency": cfg.currency.lower(),
        "market": cfg.market,
        "sorting": "price",
        "unique": "false",
        "limit": 1000,
    }
    http = session or requests
    try:
        resp = http.get(API_URL, params=params, headers={"X-Access-Token": token}, timeout=30)
    except requests.RequestException as e:
        raise TravelpayoutsError(f"request failed: {e}") from e
    if resp.status_code != 200:
        raise TravelpayoutsError(f"HTTP {resp.status_code}: {resp.text[:200]}")
    body = resp.json()
    if not body.get("success", False):
        raise TravelpayoutsError(f"API error: {body.get('error') or body}")
    return body.get("data") or []


def to_deals(rows: list[dict], search: Search, today: date) -> list[Deal]:
    """Filter raw rows down to the cheapest matching flight per date pair."""
    best: dict[str, Deal] = {}
    for row in rows:
        try:
            price = float(row["price"])
            depart = str(row["departure_at"])[:10]
            ret = str(row["return_at"])[:10] if search.is_return and row.get("return_at") else None
        except (KeyError, TypeError, ValueError):
            continue

        if price > search.max_price:
            continue
        if date.fromisoformat(depart) < today:
            continue
        if search.is_return:
            if ret is None:
                continue
            nights = (date.fromisoformat(ret) - date.fromisoformat(depart)).days
            if not (search.min_nights <= nights <= search.max_nights):
                continue
        transfers = int(row.get("transfers") or 0) + int(row.get("return_transfers") or 0)
        if search.direct_only and transfers:
            continue

        deal = Deal(
            origin=row.get("origin_airport") or row.get("origin") or search.origin,
            destination=row.get("destination_airport") or row.get("destination") or search.destination,
            depart_date=depart,
            return_date=ret,
            price=price,
            airline=row.get("airline") or "",
            flight_number=str(row.get("flight_number") or ""),
            transfers=transfers,
        )
        if deal.key not in best or price < best[deal.key].price:
            best[deal.key] = deal

    return sorted(best.values(), key=lambda d: (d.price, d.depart_date))
