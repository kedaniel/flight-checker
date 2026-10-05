from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Deal:
    """One flight (or round trip) found under the target price."""

    origin: str  # specific airport, e.g. LHR
    destination: str
    depart_date: str  # YYYY-MM-DD
    return_date: str | None
    price: float  # per person, in the configured currency
    airline: str = ""
    flight_number: str = ""
    transfers: int = 0
    # Set by verification: True (Google confirmed), False (couldn't check).
    verified: bool = False
    verified_price: float | None = None

    @property
    def key(self) -> str:
        return f"{self.origin}-{self.destination}|{self.depart_date}|{self.return_date or ''}"

    @property
    def best_price(self) -> float:
        return self.verified_price if self.verified_price is not None else self.price
