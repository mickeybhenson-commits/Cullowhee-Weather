# Upgrade pack: alerts, NWS QPF, outlook strip, bias report

Four independent pieces, added 2026-08-10 while WeatherNext access is
pending. None depends on Google; each degrades gracefully if its inputs are
absent. The confirmation path is untouched, as always.

## 1. Posture-change phone alerts — `notify_posture.py`

The feed stops being pull-only: when the operative posture changes
(NORMAL→WATCH, →WARNING, stand-downs), the publish Action pushes to your
phone via ntfy.sh. WeatherNext outlook crossings (P(WATCH) ≥ 40%) push a
lower-priority "heads-up" with a 12 h cooldown — silent until WN access is
live.

**Setup (one time, ~3 min):**
1. Invent a hard-to-guess topic name, e.g. `noah-cullowhee-x7k2m9`.
   Anyone who knows it can subscribe — treat it like a password.
2. Install the **ntfy** app (iOS/Android) → Subscribe to topic → enter it.
3. GitHub repo → Settings → Secrets and variables → Actions →
   New repository secret: name `NTFY_TOPIC`, value = your topic.
Until the secret exists the notifier idles with a log line. Others (Jim,
JCEM) can subscribe to the same topic — that's the sharing model.

## 2. NWS gridded QPF second source — `nws_qpf.py`, `ledger/fetch_nws_qpf.py`

The GSP forecast grids (2.5 km, human forecaster in the loop) join the
ledger beside Open-Meteo and, later, WeatherNext — so the bias record can
answer *which operational QPF input under-calls Cullowhee orographic rain
least*, with evidence. `outlook.json` now carries `nws_qpf24_in` per basin
as an independent cross-check (publishes even while WeatherNext is dark).
No key, no signup; api.weather.gov requires only the descriptive
User-Agent already set in the module.

## 3. Storm-watch outlook strip — `storm_watch.html`

A 7-day P(WATCH) bar strip on the storm-tracking page, from
`campus_daily` in `outlook.json`: each WeatherNext member's daily rain run
on that member's own projected soil state, so compound events (moderate
rain onto primed soils) show up as high-probability days even when the
day's QPF looks modest. Hidden until WN access is live; renders from the
fixture in testing.

## 4. Bias verification report — `ledger/bias_report.py`, `bias.html`

The "climbing out of shadow mode" evidence trail.
`bias_report.py` scores every forecast source against MRMS over paired wet
6-h windows (ratio-of-sums; wet ≥ 12.7 mm/6h; MRMS valid fraction ≥ 0.8;
cells publish only at n ≥ 8) and emits `feed/bias_report.json`. It also
prints `suggest_mult` — the upward-only correction that will seed
`WN_BIAS_MULT_JSON` after a verified season. `bias.html` renders the
report publicly, with an honest empty state until the ledger has scored a
storm. Runs on the ledger host (`deploy/qpf-bias-report.timer`, daily);
publish by committing the JSON into `feed/`.

## Deployment notes

* GitHub Actions side: automatic — the workflow gained one notifier step;
  everything else rides the existing 30-min publish.
* Ledger host side (when you stand it up): `systemctl enable --now`
  `qpf-nws.timer` and `qpf-bias-report.timer` alongside the existing
  qpf-* timers.
* New/changed files: `notify_posture.py`, `nws_qpf.py`, `bias.html`,
  `UPGRADES_ALERTS_VERIFICATION.md`, `feed_runner.py`, `storm_watch.html`,
  `.github/workflows/publish-feed.yml`, `ledger/fetch_nws_qpf.py`,
  `ledger/bias_report.py`, `deploy/qpf-nws.{service,timer}`,
  `deploy/qpf-bias-report.{service,timer}`.
