"""Check flight prices and send ntfy alerts.

Usage: python -m flight_checker [--config config.yaml] [--state state/state.json] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import date

from . import google_flights, notify, travelpayouts
from .config import Config, ConfigError, Search, load_config
from .models import Deal
from .state import State

log = logging.getLogger("flight_checker")

# Stop calling Google for the rest of the run after this many failures in a row
# (usually means requests are being blocked).
MAX_CONSECUTIVE_GOOGLE_ERRORS = 3


def find_deals(search: Search, cfg: Config, token: str, today: date) -> list[Deal]:
    current_month = today.strftime("%Y-%m")
    deals: list[Deal] = []
    for month in search.months:
        if month < current_month:
            log.info("[%s] %s is in the past, skipping", search.name, month)
            continue
        # Prices are cached per market (where the searches came from), so combine
        # several markets for better coverage. Fail only if every market fails.
        rows: list[dict] = []
        errors: list[str] = []
        for market in cfg.markets:
            try:
                rows.extend(travelpayouts.fetch_month(search, month, cfg, token, market))
            except travelpayouts.TravelpayoutsError as e:
                log.warning("[%s] %s market %s: %s", search.name, month, market, e)
                errors.append(f"{market}: {e}")
        if len(errors) == len(cfg.markets):
            raise travelpayouts.TravelpayoutsError("; ".join(errors))
        found = travelpayouts.to_deals(rows, search, today)
        log.info("[%s] %s: %d prices from Travelpayouts, %d under %s",
                 search.name, month, len(rows), len(found), search.max_price)
        deals.extend(found)
    return deals


def verify(deals: list[Deal], search: Search, cfg: Config, google_errors: list[int]) -> list[Deal]:
    """Check the cheapest deals on Google Flights. Returns the ones to alert on."""
    v = cfg.verification
    if not v.enabled:
        return deals

    keep: list[Deal] = []
    for i, deal in enumerate(deals[: v.max_checks_per_search]):
        if google_errors[0] >= MAX_CONSECUTIVE_GOOGLE_ERRORS:
            result = google_flights.CheckResult("error", detail="skipped after repeated errors")
        else:
            if i:
                time.sleep(v.delay_seconds)
            result = google_flights.check(deal, search, cfg.currency)
            google_errors[0] = google_errors[0] + 1 if result.status == "error" else 0

        log.info("[%s] verify %s (%s): %s %s", search.name, deal.key, deal.price,
                 result.status, result.price if result.price is not None else result.detail)
        if result.status == "confirmed":
            deal.verified = True
            deal.verified_price = result.price
            keep.append(deal)
        elif result.status == "error" and v.on_error == "send":
            keep.append(deal)
    # Deals past max_checks_per_search are left for later runs, once cheaper ones
    # have been alerted and drop out of the "new" list.
    return keep


def run(cfg: Config, state: State, *, tp_token: str, topic: str | None,
        ntfy_token: str | None, dry_run: bool, today: date) -> bool:
    ok = True
    google_errors = [0]
    state.prune(today)

    for search in cfg.searches:
        try:
            deals = find_deals(search, cfg, tp_token, today)
        except travelpayouts.TravelpayoutsError as e:
            log.error("[%s] Travelpayouts failed: %s", search.name, e)
            ok = False
            continue

        new = [d for d in deals if state.is_new(d, cfg.notify.renotify_drop_pct)]
        log.info("[%s] %d new or cheaper than last alert", search.name, len(new))
        if not new:
            continue

        to_send = [d for d in verify(new, search, cfg, google_errors)
                   if state.is_new(d, cfg.notify.renotify_drop_pct)]
        if not to_send:
            continue

        payload = notify.build_message(to_send, search, cfg)
        if dry_run:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            try:
                notify.send(payload, cfg.notify.server, topic, ntfy_token)
            except Exception as e:
                log.error("[%s] ntfy send failed: %s", search.name, e)
                ok = False
                continue
            log.info("[%s] sent alert with %d deals", search.name, len(to_send))
        for d in to_send:
            state.mark_sent(d)

    if not dry_run:
        state.save()
    return ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--state", default="state/state.json")
    parser.add_argument("--dry-run", action="store_true",
                        help="print notifications instead of sending, don't update state")
    parser.add_argument("--no-verify", action="store_true", help="skip Google Flights checks")
    parser.add_argument("--test-notification", action="store_true",
                        help="send one test message to the ntfy topic and exit")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    try:
        cfg = load_config(args.config)
    except (ConfigError, OSError) as e:
        log.error("%s", e)
        return 2
    if args.no_verify:
        cfg.verification.enabled = False

    topic = os.environ.get("NTFY_TOPIC")
    ntfy_token = os.environ.get("NTFY_TOKEN") or None
    tp_token = os.environ.get("TRAVELPAYOUTS_TOKEN")

    if not args.dry_run and not topic:
        log.error("NTFY_TOPIC is not set")
        return 2

    if args.test_notification:
        notify.send({"title": "✈ Flight checker is set up",
                     "message": "You'll get alerts on this topic.", "tags": ["airplane"]},
                    cfg.notify.server, topic, ntfy_token)
        log.info("test notification sent")
        return 0

    if not tp_token:
        log.error("TRAVELPAYOUTS_TOKEN is not set")
        return 2

    ok = run(cfg, State(args.state), tp_token=tp_token, topic=topic, ntfy_token=ntfy_token,
             dry_run=args.dry_run, today=date.today())
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
