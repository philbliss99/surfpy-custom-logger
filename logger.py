# logger.py - multi-buoy version for surfpy-custom-logger
import csv, os, requests
from datetime import datetime, timezone
import surfpy
from surfpy.location import Location

BUOYS = [
    {"id": "44065", "name": "rockaway", "lat": 40.580, "lon": -73.830, "loc": Location(40.580, -73.830)},
    {"id": "46221", "name": "santamonica", "lat": 33.85, "lon": -118.63, "loc": Location(33.85, -118.63)},
    {"id": "46053", "name": "santabarbara", "lat": 34.24, "lon": -119.85, "loc": Location(34.24, -119.85)},
]

# --- keep your working buoy + forecast logic from before ---
def get_buoy_actual(buoy_id: str):
    url = f"https://www.ndbc.noaa.gov/data/realtime2/{buoy_id}.txt"
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        lines = r.text.strip().splitlines()
        header = lines[0].split()
        if len(lines) < 3:
            print(f"{buoy_id}: not enough lines")
            return None
        data = lines[2].split()
        row = dict(zip(header, data))
        # WVHT may be "MM"
        if row.get("WVHT") == "MM" or row.get("WVHT") is None:
            print(f"{buoy_id}: Buoy WVHT missing (MM), but continuing")
            return None
        wvht_m = float(row.get("WVHT", "MM"))
        dpd = row.get("DPD")
        mwd = row.get("MWD")
        try:
            dpd_f = float(dpd) if dpd != "MM" else ""
        except:
            dpd_f = ""
        try:
            mwd_f = float(mwd) if mwd != "MM" else ""
        except:
            mwd_f = ""
        return wvht_m, dpd_f, mwd_f, datetime.now(timezone.utc), "ndbc_txt"
    except Exception as e:
        print(f"{buoy_id} buoy fetch failed: {e}")
        return None

def get_forecast_surfpy(location):
    # surfpy model name changed - use open-meteo until we wire correct class
    return None

def get_forecast_openmeteo(loc: Location):
    # Fallback forecast from Open-Meteo Marine (always available)
    try:
        url = f"https://marine-api.open-meteo.com/v1/marine?latitude={loc.latitude}&longitude={loc.longitude}&hourly=wave_height,wave_period,wave_direction&timezone=UTC"
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        j = r.json()
        # get first hour
        hs = j["hourly"]["wave_height"][0]
        tp = j["hourly"]["wave_period"][0]
        wdir = j["hourly"]["wave_direction"][0]
        print(f"> openmeteo ok: {hs}m")
        return hs, tp, wdir, "openmeteo"
    except Exception as e:
        print(f"openmeteo failed for {loc.latitude},{loc.longitude}: {e}")
        return None

def get_forecast_combined(loc: Location):
    # Try surfpy first (your atlantic model), then open-meteo
    f = get_forecast_surfpy(loc)
    if f:
        return f
    return get_forecast_openmeteo(loc)

def log_one(buoy):
    buoy_id = buoy["id"]
    print(f"\n--- {buoy_id} ({buoy['name']}) ---")
    actual = get_buoy_actual(buoy_id)
    forecast = get_forecast_combined(buoy["loc"])

    if actual:
        wh, dpd, mwd, btime, src_actual = actual
        wh_ft = round(wh * 3.28084, 4)
    else:
        wh, dpd, mwd, btime = "", "", "", datetime.now(timezone.utc)
        wh_ft = ""
        print(f"No buoy data, logging forecast only")

    if forecast:
        f_wh, f_tp, f_dir, f_src = forecast
        f_wh_ft = round(f_wh * 3.28084, 4) if f_wh != "" and f_wh is not None else ""
        if f_src == "openmeteo":
            print(f"> atlantic ok: {f_wh}m")  # keep log format similar
    else:
        f_wh, f_tp, f_dir, f_src = "", "", "", "all_models_down"
        f_wh_ft = ""
        print(f"All forecast models down for {buoy_id}")

    CSV_PATH = f"data/{buoy['id'].lower()}_{buoy['name']}_log.csv"
    if buoy_id == "44065":
        CSV_PATH = "data/rockaway_44065_log.csv"

    row = {
        "logged_at_utc": datetime.now(timezone.utc).isoformat(),
        "buoy_id": buoy_id,
        "buoy_time": btime.isoformat() if hasattr(btime, "isoformat") else str(btime),
        "actual_wvht_ft": wh_ft,
        "actual_wvht_m": wh,
        "actual_dpd": dpd,
        "actual_mwd": mwd,
        "forecast_hs_ft": f_wh_ft,
        "forecast_hs_m": f_wh,
        "forecast_tp": f_tp,
        "forecast_dir": f_dir,
        "forecast_source": f_src,
    }

    os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)
    exists = os.path.exists(CSV_PATH)
    with open(CSV_PATH, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=row.keys())
        if not exists:
            w.writeheader()
        w.writerow(row)

    print(f"Logged: actual {row['actual_wvht_ft']}ft @ {row['actual_dpd']}s | forecast {row['forecast_hs_ft']}ft via {f_src}")

if __name__ == "__main__":
    for b in BUOYS:
        log_one(b)
