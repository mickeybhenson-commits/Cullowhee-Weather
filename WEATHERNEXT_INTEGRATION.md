# WeatherNext 2 ensemble integration — activation runbook

**Status: ships dark.** Everything here degrades gracefully until access is
configured: the outlook feed publishes an honest `"unavailable"` status, the
ledger archiver exits clean with "skipped", and live.html shows no chips.
Nothing in the 0–6 h confirmation path (flash.html, MRMS, stage sensors,
`flood_engine`) is touched by this integration — WeatherNext feeds the
**Outlook tier only**, WATCH-capped per `flood_network`'s two-tier rule.

## What was added

| File | Change |
|---|---|
| `weathernext_source.py` | NEW — WN2 ensemble connector (BigQuery / fixture ladder), window algebra, bias multipliers |
| `flood_ensemble.py` | `ensemble_members()` — real 64-member QPF axis replaces the synthetic ±25% grid |
| `outlook_engine.py` | `forecast_basin_ens()` — per-member engine runs → stage quantiles + P(≥level), WATCH-capped |
| `wetness.py` | `project_api()` / `project_wetness_members()` — forward antecedent-state projection |
| `ledger/fetch_weathernext.py` | NEW — archives wn2-mean/p10/p50/p90 into the QPF bias ledger (hourly-disaggregation caveat in header) |
| `feed_runner.py` | `publish_outlook()` → `feed/outlook.json`, called from `main()` |
| `live.html` | Renders the feed: probability chip in the Outlook column, tooltip block, What-if ensemble readout. Render-only — no new engine port surface |
| `deploy/qpf-weathernext.{service,timer}` | systemd units, 6-hourly, offset for WN2's 6–8 h dissemination lag |
| `.github/workflows/publish-feed.yml` | env plumbing for `WN_BQ_TABLE` secret + `WN_BIAS_MULT_JSON` var |

## Activation sequence (deliberate order)

1. **Now — ledger first.** Enable `qpf-weathernext.timer` (or just leave the
   Action running). The moment access lands, the ledger starts accumulating
   (forecast, MRMS-observed) pairs. The per-cell orographic bias multiplier
   needs a **season of calendar time** — no code can substitute for it.
2. **Request access.** Complete Google's WeatherNext data request form
   (real-time tier). Historical is CC-BY-4.0 for research already.
3. **On approval:** set the `WN_BQ_TABLE` repo secret, add a
   `google-github-actions/auth` step to the workflow, and confirm the
   `[CONFIRM]` column names in `weathernext_source.py` (`WN_BQ_COLS` env
   overrides them without a code change).
4. **Shadow season.** `feed/outlook.json` publishes; chips appear on
   live.html clearly labeled "forecast evidence only". Watch it against real
   events — same discipline as the existing SHADOW-MODE caveat.
5. **After a verified season:** fit the per-basin multipliers from the
   ledger (`wn2-mean` vs MRMS), set `WN_BIAS_MULT_JSON` repo var, e.g.
   `{"CC-UP-503": 1.35, "CC-TIL-705": 1.25}`. Upward-only by construction.

## Testing without access (today)

```sh
python weathernext_source.py           # self-test, fixture path
python -c "import weathernext_source as wn; wn.make_fixture('fx.json')"
WN_FIXTURE=fx.json python feed_runner.py     # publishes a full outlook.json
WN_FIXTURE=fx.json python ledger/fetch_weathernext.py --db /tmp/test.db
```

Then open live.html — chips, tooltips, and the What-if readout render from
the fixture feed. `make_fixture(wet=False)` gives the dry-week case.

## Design invariants (do not relax)

* WeatherNext is **MODELED** provenance and **Outlook-tier only**. Its 6–8 h
  latency and 28 km grid disqualify it from the confirmation path forever —
  that's a property of the model class, not a wiring gap.
* Bias multipliers are **upward-only** (≥ 1.0): a corrected forecast may
  over-call, never under-call, an orographic event.
* live.html **renders** ensemble output; it never computes it. The JS/Python
  port-sync contract gains no new surface.
* `outlook.json` always publishes, carrying its status — an absent file reads
  as "broken"; a present file saying *why* is operational state.
