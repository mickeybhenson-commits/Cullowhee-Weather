"""
fetch_helene_forcing.py - pull the REAL Helene rain record for the LB-171
calibration and run the model side in one command.

Fetches K24A (Jackson Co. Airport AWOS) hourly logged precip from the IEM
archive for 2024-09-18 .. 2024-09-29 - the same station and endpoint pattern
live_rainfall.py already uses operationally, pointed at the archive window.

Produces:
  k24a_helene_hyeto.csv   (timestamp, inches-per-hour) for the event window
  prints p5 (Sep 20-24 antecedent) and daily event totals (Sep 25-27)
  then invokes calibrate_lb171.py --hyeto-csv ... --p5 ... automatically

Run from the repo directory (needs calibrate_lb171.py + engine files present):
  python3 fetch_helene_forcing.py
Add --no-run to only fetch/write the CSV.

CAVEAT (carry into the paper): K24A is a single valley-floor tipping bucket
~8 km north of the Long Branch basin. Orographic under-catch relative to the
basin is likely; treat this as the provisional forcing and supersede it with
the WCHRS gauge record (3 sites, in-basin) when Kinner provides it, or with
MRMS QPE area-averaged over the LB-171 polygon on noah-ops.

Standard library only.
"""

import csv
import datetime
import subprocess
import sys
import urllib.parse
import urllib.request

IEM_ASOS = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
STATION = "24A"
# UTC window generously brackets the local-time event + antecedent period
STS = "2024-09-18T00:00:00Z"
ETS = "2024-09-29T12:00:00Z"

ANTE_DAYS = [datetime.date(2024, 9, d) for d in range(20, 25)]   # p5 window
EVENT_DAYS = [datetime.date(2024, 9, d) for d in (25, 26, 27)]   # Helene
OUT_CSV = "k24a_helene_hyeto.csv"


def fetch(timeout=60):
    q = {"station": STATION, "data": "p01i", "sts": STS, "ets": ETS,
         "tz": "America/New_York", "format": "onlycomma",
         "missing": "M", "trace": "0.0001", "latlon": "no"}
    url = IEM_ASOS + "?" + urllib.parse.urlencode(q)
    req = urllib.request.Request(url, headers={"User-Agent": "cullowhee-flood/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def hourly_from_csv_text(txt):
    """IEM (station,valid,p01i) rows -> {hour_datetime: inches}. p01i is the
    precip logged in the hour before each ob; keep one (max) value per clock
    hour - identical convention to live_rainfall.airport_compute."""
    by_hour = {}
    for line in txt.splitlines():
        parts = line.split(",")
        if len(parts) < 3:
            continue
        ts = parts[1].strip()
        try:
            dt = datetime.datetime.strptime(ts[:16], "%Y-%m-%d %H:%M")
        except ValueError:
            continue                      # header / comment rows
        pval = parts[2].strip()
        if pval in ("M", "", "None", "null"):
            continue                      # missing - never counted as zero
        try:
            p = 0.0001 if pval == "T" else float(pval)
        except ValueError:
            continue
        hk = dt.replace(minute=0, second=0, microsecond=0)
        by_hour[hk] = max(by_hour.get(hk, 0.0), p)
    return by_hour


def main():
    print(f"Fetching K{STATION} hourly precip {STS} .. {ETS} from IEM ...")
    try:
        txt = fetch()
    except Exception as e:
        raise SystemExit(f"fetch failed (need network to mesonet.agron.iastate.edu): {e}")
    by_hour = hourly_from_csv_text(txt)
    if not by_hour:
        raise SystemExit("no usable precip rows returned - station gap? "
                         "Check the raw response / try the WCHRS record instead.")

    daily = {}
    for hk, p in by_hour.items():
        daily[hk.date()] = daily.get(hk.date(), 0.0) + p

    p5 = round(sum(daily.get(d, 0.0) for d in ANTE_DAYS), 2)
    ev = [round(daily.get(d, 0.0), 2) for d in EVENT_DAYS]
    print(f"\nAntecedent (Sep 20-24) p5: {p5:.2f} in")
    print("Event daily totals:")
    for d, v in zip(EVENT_DAYS, ev):
        print(f"  {d}: {v:.2f} in")
    print(f"  event total: {sum(ev):.2f} in")
    missing_hours = sum(1 for d in EVENT_DAYS for h in range(24)
                        if datetime.datetime(d.year, d.month, d.day, h) not in by_hour)
    if missing_hours:
        print(f"  NOTE: {missing_hours} event-window hours have no logged ob - "
              "single-AWOS gap risk; totals may under-read.")

    # event-window hourly hyetograph, chronological
    start = datetime.datetime(2024, 9, 25, 0, 0)
    end = datetime.datetime(2024, 9, 28, 0, 0)
    with open(OUT_CSV, "w", newline="") as f:
        w = csv.writer(f)
        hk = start
        while hk < end:
            w.writerow([hk.strftime("%Y-%m-%d %H:%M"),
                        round(by_hour.get(hk, 0.0), 4)])
            hk += datetime.timedelta(hours=1)
    print(f"\nwrote {OUT_CSV}")

    if "--no-run" in sys.argv:
        return
    cmd = [sys.executable, "calibrate_lb171.py",
           "--hyeto-csv", OUT_CSV, "--p5", str(p5)]
    print("\nrunning:", " ".join(cmd), "\n")
    subprocess.run(cmd, check=False)


if __name__ == "__main__":
    main()
