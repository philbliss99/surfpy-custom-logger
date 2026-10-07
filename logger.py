import csv, os, requests
from datetime import datetime, timezone

BUOYS = [
    {"id": "44065", "name": "rockaway", "lat": 40.369, "lon": -73.703, "ndbc": "44065"},
    {"id": "46221", "name": "santamonica", "lat": 33.85, "lon": -118.64, "ndbc": "46221"},
    {"id": "46053", "name": "santabarbara", "lat": 34.242, "lon": -119.773, "ndbc": "46053"},
]

def fetch_ndbc_realtime(buoy_id):
    # NDBC realtime2 - returns WVHT in meters
    url = f"https://www.ndbc.noaa.gov/data/realtime2/{buoy_id}.txt"
    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        lines = r.text.strip().splitlines()
        # header on first line, data on second
        if len(lines) < 2:
            return None, None, "no data lines"
        header = lines[0].split()
        data = lines[1].split()
        d = dict(zip(header, data))
        wvht = d.get("WVHT")
        if wvht == "MM" or wvht is None:
            return None, None, f"WVHT missing ({wvht})"
        # dominant period
        dpd = d.get("DPD")
        if dpd == "MM":
            dpd = None
        return float(wvht), float(dpd) if dpd else None, "ok"
    except Exception as e:
        return None, None, str(e)

def fetch_openmeteo_forecast(lat, lon):
    # Open-Meteo marine forecast - wave_height in meters
    url = f"https://marine-api.open-meteo.com/v1/marine?latitude={lat}&longitude={lon}&hourly=wave_height,wave_period&forecast_days=1&timezone=UTC"
    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        j = r.json()
        wh = j.get("hourly", {}).get("wave_height", [])
        wp = j.get("hourly", {}).get("wave_period", [])
        if not wh or wh[0] is None:
            return None, None, "openmeteo empty"
        # first hour = now forecast
        return float(wh[0]), float(wp[0]) if wp and wp[0] else None, "ok"
    except Exception as e:
        return None, None, str(e)

os.makedirs("data", exist_ok=True)
now = datetime.now(timezone.utc).isoformat()

for b in BUOYS:
    buoy_id = b["id"]
    name = b["name"]
    lat = b["lat"]
    lon = b["lon"]
    ndbc_id = b["ndbc"]

    print(f"\n--- {buoy_id} ({name}) ---")

    actual_m, actual_period, actual_status = fetch_ndbc_realtime(ndbc_id)
    if actual_m is None:
        print(f"Buoy WVHT missing ({actual_status}), but continuing to forecast")
        # Still log a row with actual = empty so we track gaps

    forecast_m, forecast_period, forecast_status = fetch_openmeteo_forecast(lat, lon)
    if forecast_m is not None:
        print(f"> openmeteo ok: {forecast_m}m")
    else:
        print(f"> openmeteo fail: {forecast_status}")

    # Convert to ft for your CSV (you were using ft before)
    def m_to_ft(m):
        return round(m * 3.28084, 2) if m is not None else ""

    csv_path = f"data/{buoy_id}_{name}_log.csv"
    header = ["logged_at_utc","buoy_id","buoy_name","lat","lon",
              "actual_hs_m","actual_hs_ft","actual_period_s",
              "forecast_hs_m","forecast_hs_ft","forecast_period_s",
              "forecast_source","actual_status","forecast_status"]

    row = {
        "logged_at_utc": now,
        "buoy_id": buoy_id,
        "buoy_name": name,
        "lat": lat,
        "lon": lon,
        "actual_hs_m": actual_m if actual_m is not None else "",
        "actual_hs_ft": m_to_ft(actual_m),
        "actual_period_s": actual_period if actual_period is not None else "",
        "forecast_hs_m": forecast_m if forecast_m is not None else "",
        "forecast_hs_ft": m_to_ft(forecast_m),
        "forecast_period_s": forecast_period if forecast_period is not None else "",
        "forecast_source": "openmeteo" if forecast_m is not None else "",
        "actual_status": actual_status,
        "forecast_status": forecast_status,
    }

    write_header = not os.path.exists(csv_path)
    with open(csv_path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header)
        if write_header:
            w.writeheader()
        w.writerow(row)

    print(f"Logged: actual {row['actual_hs_ft']}ft @ {row['actual_period_s']}s | forecast {row['forecast_hs_ft']}ft @ {row['forecast_period_s']}s via {row['forecast_source']}")

print("\nDone")
