# logger.py - put this OUTSIDE the surfpy folder, in your project root
import csv
import os
import time
from datetime import datetime, timezone
import requests

import surfpy
# surfpy's core classes you already saw in surfpy/
from surfpy.location import Location

# --- CONFIG FOR YOU ---
ROCKAWAY = Location(40.580, -73.830) # Rockaway Beach ~ 40.58N, 73.83W
BUOY_ID = "44065" # NY Harbor Entrance - 15 NM SE of Breezy Point
CSV_PATH = "data/rockaway_44065_log.csv"

def get_buoy_actual(buoy_id: str, lookback_hours=6):
    """
    Gets actual from NDBC, but searches back up to 6 hours
    for a valid WVHT if the latest is MM.
    """
    url = f"https://www.ndbc.noaa.gov/data/realtime2/{buoy_id}.txt"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        lines = r.text.strip().split("\n")
        header = lines[0].split()

        # Lines 2 onwards are data, newest first. Line 0=header, 1=units
        for i in range(2, 2 + lookback_hours + 1):
            if i >= len(lines):
                break
            data = lines[i].split()
            if len(data) < len(header):
                continue
            row = dict(zip(header, data))

            if row.get("WVHT") == "MM" or row.get("WVHT") is None:
                continue # this hour is missing, try previous hour

            # Found a valid one!
            wvht_m = float(row["WVHT"])
            dpd_raw = row.get("DPD", "MM")
            mwd_raw = row.get("MWD", "MM")
            dpd = float(dpd_raw) if dpd_raw!= "MM" else None
            mwd = float(mwd_raw) if mwd_raw!= "MM" else None

            wvht_ft = wvht_m * 3.28084
            print(f"Using buoy reading from {i-2}h ago (line {i})")
            return {
                "wvht_ft": wvht_ft,
                "wvht_m": wvht_m,
                "dpd": dpd,
                "mwd": mwd,
                "raw_time": f"{row['#YY']} {row['MM']} {row['DD']} {row['hh']} {row['mm']}"
            }

        print(f"Buoy {buoy_id} reporting MM for last {lookback_hours}h, truly offline")
        return None

    except Exception as e:
        print(f"Buoy fetch failed: {e}")
        return None

def get_surfpy_forecast(location: Location):
    rockaway_wave_location = surfpy.Location(
        40.58, -73.83, altitude=30.0, name='Rockaway',
        depth=30.0, angle=145.0, slope=0.02
    )

    # Try in order: Atlantic (best for Rockaway), then Global (backup)
    models_to_try = [
        ("atlantic", surfpy.wavemodel.atlantic_gfs_wave_model()),
        ("global", surfpy.wavemodel.global_gfs_wave_model()), # this exists in surfpy too
    ]

    for model_name, wave_model in models_to_try:
        try:
            print(f'Trying {model_name} model...')
            wave_grib_data = wave_model.fetch_grib_datas(0, 3) # 0-3h, not just 0-1h

            if not wave_grib_data:
                print(f" -> {model_name} download empty, trying next model")
                continue

            raw_wave_data = wave_model.parse_grib_datas(rockaway_wave_location, wave_grib_data)
            if not raw_wave_data:
                print(f" -> {model_name} parse empty, trying next model")
                continue

            data = wave_model.to_buoy_data(raw_wave_data)
            first = data[0]
            first.solve_breaking_wave_heights(rockaway_wave_location)
            first.change_units(surfpy.units.Units.english)

            print(f" -> {model_name} succeeded")
            return {
                "forecast_hs_ft": float(first.wave_summary.wave_height),
                "forecast_tp": float(first.wave_summary.period) if hasattr(first.wave_summary, 'period') else None,
                "forecast_dir": float(first.wave_summary.direction) if hasattr(first.wave_summary, 'direction') else None,
                "forecast_source": f"{model_name}_gfs_wave_model"
            }

        except Exception as e:
            print(f" -> {model_name} failed with {e}, trying next")
            continue

    # If we get here, both models were down - still log buoy, forecast=None
    print("All wave models down right now (storm overload), logging buoy only")
    return {"forecast_hs_ft": None, "forecast_tp": None, "forecast_dir": None, "forecast_source": "all_models_down"}

def main():
    os.makedirs("data", exist_ok=True)
    file_exists = os.path.exists(CSV_PATH)

    actual = get_buoy_actual(BUOY_ID)
    # Only fetch forecast if we have an actual to pair it with
    if not actual:
        print("No buoy data right now, skipping this run.")
        return

    forecast = get_surfpy_forecast(ROCKAWAY) # moved after the check
    # if forecast failed, we still log the buoy actual with None forecast
    # so you don't lose data
    if not actual:
        print("No buoy data right now, skipping this run.")
        return

    now = datetime.now(timezone.utc).isoformat()

    row = {
        "logged_at_utc": now,
        "location_lat": ROCKAWAY.latitude,
        "location_lon": ROCKAWAY.longitude,
        "buoy_id": BUOY_ID,
        "buoy_time": actual["raw_time"],
        "actual_wvht_ft": actual["wvht_ft"],
        "actual_wvht_m": actual["wvht_m"],
        "actual_dpd": actual["dpd"],
        "actual_mwd": actual["mwd"],
        "forecast_hs_ft": forecast["forecast_hs_ft"],
        "forecast_tp": forecast["forecast_tp"],
        "forecast_dir": forecast["forecast_dir"],
        "forecast_source": forecast["forecast_source"],
    }

    # For Phase 1 bias, the most important columns are actual vs forecast
    with open(CSV_PATH, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=row.keys())
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)

    print(f"Logged: actual {row['actual_wvht_ft']:.2f}ft @ {row['actual_dpd']}s {row['actual_mwd']}deg | forecast {row['forecast_hs_ft']}")

if __name__ == "__main__":
    main()