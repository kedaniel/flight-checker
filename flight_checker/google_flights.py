"""Re-check Travelpayouts matches against live Google Flights prices."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .config import Search

log = logging.getLogger(__name__)


@dataclass
class CheckResult:
    status: str  # "confirmed", "too_expensive" or "error"
    price: float | None = None
    detail: str = ""


def cheapest_price(
    origin: str,
    destination: str,
    depart_date: str,
    return_date: str | None,
    currency: str,
    direct_only: bool,
) -> float | None:
    """Cheapest per-person price Google Flights shows, or None if no flights.

    Raises on network/parsing problems.
    """
    from fast_flights import FlightQuery, FlightsNotFound, Passengers, create_query, get_flights

    legs = [FlightQuery(date=depart_date, from_airport=origin, to_airport=destination)]
    if return_date:
        legs.append(FlightQuery(date=return_date, from_airport=destination, to_airport=origin))
    query = create_query(
        flights=legs,
        trip="round-trip" if return_date else "one-way",
        seat="economy",
        passengers=Passengers(adults=1),
        currency=currency,
        language="en",
        max_stops=0 if direct_only else None,
    )
    try:
        results = get_flights(query)
    except FlightsNotFound:
        return None
    prices = [f.price for f in results if f.price]
    return float(min(prices)) if prices else None


def check(deal, search: Search, currency: str) -> CheckResult:
    try:
        price = cheapest_price(
            deal.origin, deal.destination, deal.depart_date, deal.return_date,
            currency, search.direct_only,
        )
    except Exception as e:  # network errors, blocked requests, page format changes
        log.warning("Google Flights check failed for %s: %s", deal.key, e)
        return CheckResult("error", detail=str(e)[:200])
    if price is None:
        return CheckResult("error", detail="no flights returned")
    if price <= search.max_price:
        return CheckResult("confirmed", price)
    return CheckResult("too_expensive", price)
