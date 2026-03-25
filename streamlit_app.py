@st.cache_data(ttl=900)
def fetch_best_7day_forecast():
    daily_vars = (
        "weathercode,"
        "temperature_2m_max,"
        "temperature_2m_min,"
        "precipitation_sum,"
        "precipitation_probability_max,"
        "windspeed_10m_max,"
        "windgusts_10m_max,"
        "winddirection_10m_dominant"
    )

    common = {
        "latitude": LAT,
        "longitude": LON,
        "daily": daily_vars,
        "temperature_unit": "fahrenheit",
        "precipitation_unit": "inch",
        "windspeed_unit": "mph",
        "timezone": "America/New_York",
        "forecast_days": 7,
    }

    def fetch_json(url, params, name):
        try:
            r = safe_get(url, params=params, timeout=12)
            r.raise_for_status()
            j = r.json()
            return {"ok": True, "name": name, "daily": j.get("daily", {})}
        except Exception as e:
            return {"ok": False, "name": name, "daily": {}, "error": str(e)}

    # Short range
    hrrr = fetch_json(
        "https://api.open-meteo.com/v1/gfs",
        {**common, "models": "hrrr_conus"},
        "HRRR",
    )

    # Best 3–7 day CONUS calibrated blend
    nbm = fetch_json(
        "https://api.open-meteo.com/v1/gfs",
        {**common, "models": "nbm_conus"},
        "NBM",
    )

    # Strong global fallback
    ecmwf = fetch_json(
        "https://api.open-meteo.com/v1/ecmwf",
        common,
        "ECMWF",
    )

    # Final fallback
    gfs = fetch_json(
        "https://api.open-meteo.com/v1/gfs",
        {**common, "models": "gfs_seamless"},
        "GFS",
    )

    sources = [hrrr, nbm, ecmwf, gfs]
    errors = {s["name"]: s.get("error") for s in sources if not s["ok"]}

    def day_from_source(src, i):
        d = src.get("daily", {})
        if not d or i >= len(d.get("time", [])):
            return None
        return {
            "date": d["time"][i],
            "hi": round(d["temperature_2m_max"][i]) if d["temperature_2m_max"][i] is not None else None,
            "lo": round(d["temperature_2m_min"][i]) if d["temperature_2m_min"][i] is not None else None,
            "precip": round(d["precipitation_sum"][i] or 0, 2),
            "pop": int(round(d["precipitation_probability_max"][i] or 0)),
            "wind": round(d["windspeed_10m_max"][i] or 0),
            "gust": round(d["windgusts_10m_max"][i] or 0),
            "wind_dir": d["winddirection_10m_dominant"][i] or 0,
            "code": d["weathercode"][i] or 0,
            "desc": weather_desc(d["weathercode"][i] or 0),
            "model": src["name"],
        }

    days = []
    today = now_local().date()

    for i in range(7):
        # Preferred order by lead time:
        # days 0–1: HRRR > NBM > ECMWF > GFS
        # days 2–6: NBM > ECMWF > GFS > HRRR
        priority = [hrrr, nbm, ecmwf, gfs] if i <= 1 else [nbm, ecmwf, gfs, hrrr]

        chosen = None
        for src in priority:
            if src["ok"]:
                cand = day_from_source(src, i)
                if cand is not None:
                    chosen = cand
                    break

        if chosen is None:
            chosen = {
                "date": (today + timedelta(days=i)).strftime("%Y-%m-%d"),
                "hi": 60,
                "lo": 40,
                "precip": 0.0,
                "pop": 0,
                "wind": 0,
                "gust": 0,
                "wind_dir": 0,
                "code": 0,
                "desc": "Unavailable",
                "model": "N/A",
            }

        dt = datetime.strptime(chosen["date"], "%Y-%m-%d")
        chosen["label"] = dt.strftime("%a %m/%d")
        days.append(chosen)

    return ok_payload(
        data={"days": days, "errors": errors},
        source="HRRR/NBM/ECMWF/GFS",
    )
