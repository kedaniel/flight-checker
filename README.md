# flight-checker

Get a phone notification (via [ntfy](https://ntfy.sh)) when there's a flight **any day in a
chosen month** under **your price**.

How it works, every 6 hours on GitHub Actions:

1. **Travelpayouts (Aviasales) Data API** returns the cheapest known prices for every day of the
   month in one call. These come from recent searches, so they can be a little out of date.
2. Matches under your price are **re-checked on Google Flights** for live prices. Flights that are
   now too expensive are dropped. If Google can't be reached, the alert still goes out, marked
   *unverified* (configurable).
3. New deals are sent to your **ntfy topic**, one message per search, with buttons that open the
   matching **Skyscanner** search for that date.
4. Flights you've already been alerted about aren't sent again unless they get 10%+ cheaper.

Example notification:

```
✈ London → Barcelona: 3 flights under £60
£38 · Tue 8 Dec · STN→BCN · FR · direct · ✓ Google
£45 · Sat 12 Dec · LGW→BCN · VY · direct · ✓ Google
£57 · Thu 17 Dec · LTN→BCN · U2 · direct · unverified
[£38 Tue 8 Dec] [£45 Sat 12 Dec] [£57 Thu 17 Dec]
```

## Setup

1. **ntfy app**: install it ([Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy) /
   [iOS](https://apps.apple.com/app/ntfy/id1625396347)), tap **+** and subscribe to a topic. Pick a
   long, hard-to-guess name (e.g. `flights-k7q2x9m4`). Anyone who knows the name can read it.
2. **Travelpayouts token**: sign up free at [travelpayouts.com](https://www.travelpayouts.com/),
   then copy your API token from *Profile → API token*.
3. **GitHub secrets**: in the repo go to *Settings → Secrets and variables → Actions* and add:
   - `TRAVELPAYOUTS_TOKEN`: your Travelpayouts token
   - `NTFY_TOPIC`: your topic name
   - `NTFY_TOKEN` (optional): access token if your ntfy server/topic needs auth
4. **Edit `config.yaml`**: routes, month(s), maximum price, currency (see below).
5. **Test**: *Actions → Check flight prices → Run workflow*, tick **Only send a test message**.
   You should get a notification within seconds. Then run it once without the tick.

Scheduled runs then happen every 6 hours. Change the `cron` line in
`.github/workflows/check-flights.yml` to check more or less often.

## config.yaml

```yaml
currency: GBP
markets: [uk]
skyscanner_domain: www.skyscanner.net

searches:
  - name: London → Barcelona
    origin: LON            # airport (LHR) or city code (LON = all London airports)
    destination: BCN
    month: 2026-12         # or months: [2026-12, 2027-01]
    max_price: 60          # per person
    trip: one-way          # or return
    direct_only: false

  - name: Weekend in Lisbon
    origin: MAN
    destination: LIS
    month: 2027-02
    max_price: 150
    trip: return
    nights: [2, 4]         # stay length: 2 to 4 nights
    adults: 2              # used for the Skyscanner link; prices stay per person
```

Other settings (with defaults) are in `config.yaml`: how many Google checks per search, what to
do when Google can't be checked (`on_error: send` or `skip`), how much cheaper a flight must get
before it's re-sent, and when an alert is marked high priority.

Searches for months that have already passed are skipped automatically.

## Running locally

```bash
pip install -r requirements-dev.txt
export TRAVELPAYOUTS_TOKEN=... NTFY_TOPIC=...
python -m flight_checker --dry-run        # print what would be sent, don't send or save
python -m flight_checker                  # real run
python -m flight_checker --no-verify      # skip the Google Flights check
python -m flight_checker --test-notification
pytest -q
```

## Things to know

- Prices are economy, per person. Travelpayouts only has prices for routes people have searched
  recently, so very quiet routes may show few or no results.
- The Google Flights check uses the unofficial
  [fast-flights](https://github.com/AWeirdDev/fast-flights) library. If Google changes its page or
  blocks the runner, alerts still arrive but marked *unverified*.
- Alert history (`state/state.json`) is kept in the GitHub Actions cache. If the cache is cleared
  you may get one repeat alert for deals you've already seen.
- GitHub turns off scheduled workflows after 60 days with no repo activity. Re-enable them in
  the Actions tab, or push any commit.
