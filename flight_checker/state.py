"""Remember which deals were already sent so the same flight isn't alerted every run."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from .models import Deal


class State:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.sent: dict[str, float] = {}
        if self.path.exists():
            try:
                self.sent = json.loads(self.path.read_text()).get("sent", {})
            except (json.JSONDecodeError, AttributeError):
                self.sent = {}

    def is_new(self, deal: Deal, renotify_drop_pct: float) -> bool:
        """New flight, or one that dropped by at least renotify_drop_pct since last alert."""
        last = self.sent.get(deal.key)
        if last is None:
            return True
        return deal.best_price <= last * (1 - renotify_drop_pct / 100)

    def mark_sent(self, deal: Deal) -> None:
        self.sent[deal.key] = deal.best_price

    def prune(self, today: date) -> None:
        """Forget flights that have already departed."""
        self.sent = {
            k: v for k, v in self.sent.items()
            if date.fromisoformat(k.split("|")[1]) >= today
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"sent": self.sent}, indent=2, sort_keys=True))
