from datetime import date
from pathlib import Path

import pytest

from flight_checker import google_flights, main, notify, travelpayouts
from flight_checker.config import ConfigError, load_config
from flight_checker.google_flights import CheckResult
from flight_checker.models import Deal
from flight_checker.state import State

TODAY = date(2026, 11, 10)


def write_config(tmp_path: Path, search_yaml: str, extra: str = "") -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(
        "currency: GBP\nmarket: uk\nsearches:\n" + search_yaml + extra, encoding="utf-8"
    )
    return p


ONE_WAY = """
  - name: London → Barcelona
    origin: lon
    destination: BCN
    month: 2026-11
    max_price: 60
"""

RETURN = """
  - origin: LON
    destination: BCN
    months: [2026-11]
    max_price: 120
    trip: return
    nights: [3, 5]
    adults: 2
"""


def row(depart, price, ret=None, transfers=0, origin="LHR"):
    r = {
        "origin": "LON", "destination": "BCN", "origin_airport": origin,
        "destination_airport": "BCN", "price": price, "airline": "VY",
        "flight_number": "7001", "departure_at": f"{depart}T07:00:00+00:00",
        "transfers": transfers, "return_transfers": 0,
    }
    if ret:
        r["return_at"] = f"{ret}T19:00:00+01:00"
    return r


# --- config ---------------------------------------------------------------

def test_config_defaults(tmp_path):
    cfg = load_config(write_config(tmp_path, ONE_WAY))
    s = cfg.searches[0]
    assert (s.origin, s.months, s.trip, s.adults) == ("LON", ["2026-11"], "one-way", 1)
    assert cfg.markets == ["uk"]
    assert cfg.verification.enabled and cfg.notify.server == "https://ntfy.sh"


def test_config_return_nights(tmp_path):
    s = load_config(write_config(tmp_path, RETURN)).searches[0]
    assert s.is_return and (s.min_nights, s.max_nights) == (3, 5)


@pytest.mark.parametrize("bad", [
    ONE_WAY.replace("2026-11", "Nov 2026"),
    ONE_WAY.replace("lon", "London"),
    ONE_WAY.replace("max_price: 60", "max_price: 0"),
    RETURN.replace("    nights: [3, 5]\n", ""),
])
def test_config_rejects_bad_values(tmp_path, bad):
    with pytest.raises(ConfigError):
        load_config(write_config(tmp_path, bad))


# --- Travelpayouts filtering ---------------------------------------------

def test_to_deals_one_way_filters_and_dedupes(tmp_path):
    s = load_config(write_config(tmp_path, ONE_WAY)).searches[0]
    rows = [
        row("2026-11-20", 55),
        row("2026-11-20", 45),            # cheaper duplicate for the same flight date
        row("2026-11-21", 61),            # over max price
        row("2026-11-05", 20),            # already in the past
        row("2026-11-22", 30, origin="STN"),
    ]
    deals = travelpayouts.to_deals(rows, s, TODAY)
    assert [(d.origin, d.depart_date, d.price) for d in deals] == [
        ("STN", "2026-11-22", 30), ("LHR", "2026-11-20", 45),
    ]


def test_to_deals_return_nights_and_direct(tmp_path):
    cfg = load_config(write_config(tmp_path, RETURN.replace("adults: 2", "direct_only: true")))
    s = cfg.searches[0]
    rows = [
        row("2026-11-20", 100, ret="2026-11-24"),             # 4 nights ✓
        row("2026-11-20", 90, ret="2026-11-27"),              # 7 nights ✗
        row("2026-11-21", 80, ret="2026-11-24", transfers=1),  # not direct ✗
        row("2026-11-22", 70),                                # no return date ✗
    ]
    deals = travelpayouts.to_deals(rows, s, TODAY)
    assert [(d.depart_date, d.return_date) for d in deals] == [("2026-11-20", "2026-11-24")]


class FakeResp:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, str(body)

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, resp):
        self.resp, self.calls = resp, []

    def get(self, url, params, headers, timeout):
        self.calls.append((url, params, headers))
        return self.resp


def test_fetch_month_params_and_errors(tmp_path):
    cfg = load_config(write_config(tmp_path, RETURN))
    sess = FakeSession(FakeResp(200, {"success": True, "data": [row("2026-11-20", 10)]}))
    rows = travelpayouts.fetch_month(cfg.searches[0], "2026-11", cfg, "tok", "uk", sess)
    assert len(rows) == 1
    _, params, headers = sess.calls[0]
    assert params["departure_at"] == "2026-11" and params["one_way"] == "false"
    assert params["currency"] == "gbp" and params["market"] == "uk"
    assert headers["X-Access-Token"] == "tok"

    with pytest.raises(travelpayouts.TravelpayoutsError):
        travelpayouts.fetch_month(cfg.searches[0], "2026-11", cfg, "tok", "uk",
                                  FakeSession(FakeResp(401, {"error": "bad token"})))


# --- Google Flights check --------------------------------------------------

class FakeFlight:
    def __init__(self, price):
        self.price = price


def test_google_check(monkeypatch, tmp_path):
    import fast_flights

    s = load_config(write_config(tmp_path, ONE_WAY)).searches[0]
    deal = Deal("LHR", "BCN", "2026-11-20", None, 45)
    seen = {}

    def fake_get(query):
        seen["url"] = query.url()
        return [FakeFlight(70), FakeFlight(52)]

    monkeypatch.setattr(fast_flights, "get_flights", fake_get)
    assert google_flights.check(deal, s, "GBP") == CheckResult("confirmed", 52.0)
    assert "curr=GBP" in seen["url"]

    monkeypatch.setattr(fast_flights, "get_flights", lambda q: [FakeFlight(75)])
    assert google_flights.check(deal, s, "GBP").status == "too_expensive"

    def boom(q):
        raise RuntimeError("blocked")

    monkeypatch.setattr(fast_flights, "get_flights", boom)
    assert google_flights.check(deal, s, "GBP").status == "error"


# --- state -------------------------------------------------------------------

def test_state_dedup_and_prune(tmp_path):
    st = State(tmp_path / "s.json")
    d = Deal("LHR", "BCN", "2026-11-20", None, 50)
    assert st.is_new(d, 10)
    st.mark_sent(d)
    assert not st.is_new(Deal("LHR", "BCN", "2026-11-20", None, 46), 10)  # only 8% cheaper
    assert st.is_new(Deal("LHR", "BCN", "2026-11-20", None, 45), 10)      # 10% cheaper
    st.mark_sent(Deal("LHR", "BCN", "2026-11-01", None, 50))
    st.save()

    st2 = State(tmp_path / "s.json")
    st2.prune(TODAY)
    assert list(st2.sent) == [d.key]


# --- notification ----------------------------------------------------------

def test_build_message(tmp_path):
    cfg = load_config(write_config(tmp_path, RETURN))
    s = cfg.searches[0]
    deals = [
        Deal("LHR", "BCN", "2026-11-20", "2026-11-24", 110, airline="VY"),
        Deal("STN", "BCN", "2026-11-21", "2026-11-24", 85, airline="FR", transfers=1,
             verified=True, verified_price=80),
    ]
    p = notify.build_message(deals, s, cfg)
    assert p["title"] == "✈ LON→BCN: 2 flights under £120"
    first, second = p["message"].splitlines()[:2]
    assert first == "£80 · Sat 21 Nov → Tue 24 Nov (3n) · STN→BCN · FR · 1 stop · ✓ Google"
    assert second.endswith("VY · direct · unverified")
    assert "per person (2 adults)" in p["message"]
    assert p["priority"] == 4 and "fire" in p["tags"]  # 80 <= 0.75 * 120
    assert p["click"] == (
        "https://www.skyscanner.net/transport/flights/stn/bcn/261121/261124/"
        "?adultsv2=2&cabinclass=economy&rtn=1"
    )
    assert len(p["actions"]) == 2 and p["actions"][0]["label"] == "£80 Sat 21 Nov"


# --- full run ----------------------------------------------------------------

@pytest.fixture
def wired(monkeypatch, tmp_path):
    cfg = load_config(write_config(tmp_path, ONE_WAY, "\nverification:\n  delay_seconds: 0\n"))
    rows = [row("2026-11-20", 45), row("2026-11-22", 50), row("2026-11-25", 58)]
    google = {"2026-11-20": CheckResult("confirmed", 47.0),
              "2026-11-22": CheckResult("too_expensive", 70.0),
              "2026-11-25": CheckResult("error", detail="blocked")}
    sent = []
    monkeypatch.setattr(travelpayouts, "fetch_month", lambda *a, **k: rows)
    monkeypatch.setattr(google_flights, "check", lambda d, s, c: google[d.depart_date])
    monkeypatch.setattr(notify, "send", lambda payload, *a: sent.append(payload))
    return cfg, State(tmp_path / "state.json"), sent, google


def test_run_verifies_sends_and_dedupes(wired):
    cfg, st, sent, _ = wired
    kw = dict(tp_token="t", topic="topic", ntfy_token=None, dry_run=False, today=TODAY)

    assert main.run(cfg, st, **kw)
    assert len(sent) == 1
    lines = sent[0]["message"].splitlines()
    assert lines[0].startswith("£47 · Fri 20 Nov") and lines[0].endswith("✓ Google")
    assert lines[1].startswith("£58 · Wed 25 Nov") and lines[1].endswith("unverified")
    assert len(lines) == 2  # 22 Nov was too expensive on Google

    # Second run: nothing new, so nothing sent.
    assert main.run(cfg, State(st.path), **kw)
    assert len(sent) == 1


def test_run_on_error_skip(wired):
    cfg, st, sent, _ = wired
    cfg.verification.on_error = "skip"
    main.run(cfg, st, tp_token="t", topic="x", ntfy_token=None, dry_run=False, today=TODAY)
    assert len(sent[0]["message"].splitlines()) == 1


def test_run_dry_run_prints_and_keeps_state(wired, capsys):
    cfg, st, sent, _ = wired
    main.run(cfg, st, tp_token="t", topic=None, ntfy_token=None, dry_run=True, today=TODAY)
    assert sent == [] and "£47" in capsys.readouterr().out
    assert not st.path.exists()


def test_run_reports_travelpayouts_failure(wired, monkeypatch):
    cfg, st, sent, _ = wired

    def fail(*a, **k):
        raise travelpayouts.TravelpayoutsError("HTTP 500")

    monkeypatch.setattr(travelpayouts, "fetch_month", fail)
    assert not main.run(cfg, st, tp_token="t", topic="x", ntfy_token=None,
                        dry_run=False, today=TODAY)
    assert sent == []


def test_past_months_skipped(wired):
    cfg, st, sent, _ = wired
    main.run(cfg, st, tp_token="t", topic="x", ntfy_token=None, dry_run=False,
             today=date(2026, 12, 1))
    assert sent == []


def test_markets_combined_and_partial_failure(monkeypatch, tmp_path):
    cfg = load_config(write_config(tmp_path, ONE_WAY, "\nmarkets: [us, ru]\n"))
    assert cfg.markets == ["us", "ru"]
    s = cfg.searches[0]

    def fetch(search, month, cfg, token, market):
        if market == "ru":
            raise travelpayouts.TravelpayoutsError("HTTP 500")
        return [row("2026-11-20", 45)]

    monkeypatch.setattr(travelpayouts, "fetch_month", fetch)
    assert len(main.find_deals(s, cfg, "t", TODAY)) == 1

    cfg.markets = ["ru"]
    with pytest.raises(travelpayouts.TravelpayoutsError):
        main.find_deals(s, cfg, "t", TODAY)
