import csv, os, sys
from datetime import datetime, timezone, timedelta
import requests
import surfpy
from surfpy.buoystation import BuoyStation

BUOY_ID = "44065"
LAT, LON = 40.58, -73.83
CSV_PATH = "data/rockaway_44065_log.csv"

def make_rockaway_loc():
    loc = surfpy.Location(LAT, LON, altitude=0.0, name='Rockaway Beach')
    loc.depth = 20.0
    loc.angle = 180.0 # Rockaway faces south
    loc.slope = 0.02
    return loc

def get_buoy_actual(loc):
    try:
        r = requests.get(f"https://www.ndbc.noaa.gov/data/realtime2/{BUOY_ID}.txt", timeout=15)
        lines = r.text.strip().splitlines()
        if len(lines) > 2:
            p = lines[2].split()
            # NDBC format: YY MM DD hh mm WDIR WSPD GST WVHT DPD APD MWD...
            # Handle MM = missing
            def f(i):
                try:
                    v = p[i]
                    return float(v) if v!= 'MM' else None
                except:
                    return None
            wh = f(8); dpd = f(9); mwd = f(11)
            if wh is None:
                print(f"Buoy WVHT missing (MM), but continuing")
                # return None for actuals but let forecast log
                return None
            return wh, dpd, mwd, datetime.now(timezone.utc), "ndbc_txt"
    except Exception as e:
        print(f"ndbc failed: {e}")
    return None

def get_forecast_surfpy(loc):
    models = []
    try: models.append(("atlantic", surfpy.wavemodel.atlantic_gfs_wave_model()))
    except: pass
    try: models.append(("global", surfpy.wavemodel.global_gfs_wave_model()))
    except: pass
    try: models.append(("gfs_wave", surfpy.wavemodel.gfs_wave_model()))
    except: pass

    buoy = BuoyStation(BUOY_ID, loc)
    for name, model in models:
        try:
            print(f"Trying {name} model...")
            bulletin = buoy.fetch_wave_forecast_bulletin(model)
            if bulletin and len(bulletin) > 0:
                # closest to now+6h
                target = datetime.now(timezone.utc) + timedelta(hours=6)
                best = min(bulletin, key=lambda x: abs((x.date - target).total_seconds()) if hasattr(x,'date') else 0)
                # wave_summary is the deep-water forecast before shoaling
                hs = best.wave_summary.wave_height if hasattr(best,'wave_summary') else best.maximum_breaking_height
                tp = best.wave_summary.period if hasattr(best,'wave_summary') else 0
                wdir = best.wave_summary.direction if hasattr(best,'wave_summary') else 0
                print(f"> {name} ok: {hs}m")
                return hs, tp, wdir, name
            else:
                print(f"> {name} parse empty, trying next model")
        except Exception as e:
            print(f"> {name} error {e}")
    return None

def get_forecast_openmeteo():
    try:
        print("Trying open-meteo fallback...")
        url = f"https://marine-api.open-meteo.com/v1/marine?latitude={LAT}&longitude={LON}&hourly=wave_height,wave_period,wave_direction&timezone=UTC"
        j = requests.get(url, timeout=15).json()
        h = j['hourly']
        target = datetime.now(timezone.utc) + timedelta(hours=6)
        times = [datetime.fromisoformat(t.replace('Z','+00:00')) for t in h['time']]
        idx = min(range(len(times)), key=lambda i: abs((times[i]-target).total_seconds()))
        return h['wave_height'][idx], h['wave_period'][idx], h['wave_direction'][idx], "open-meteo"
    except Exception as e:
        print(f"open-meteo failed: {e}")
        return None

loc = make_rockaway_loc()
actual = get_buoy_actual(loc)
forecast = get_forecast_surfpy(loc)
if not forecast:
    forecast = get_forecast_openmeteo()

if actual:
    wh, dpd, mwd, btime, src_actual = actual
    wh_ft = round(wh*3.28084,4)
else:
    print("No buoy data, logging forecast only")
    wh, dpd, mwd, btime, src_actual = "", "", "", datetime.now(timezone.utc), "buoy_missing"
    wh_ft = ""

if forecast:
    f_wh, f_tp, f_dir, f_src = forecast
    f_wh_ft = round(f_wh*3.28084,4) if f_wh else ""
else:
    f_wh, f_tp, f_dir, f_src = "", "", "", "all_models_down"
    f_wh_ft = ""

row = {
    "logged_at_utc": datetime.now(timezone.utc).isoformat(),
    "buoy_id": BUOY_ID,
    "buoy_time": btime.isoformat(),
    "actual_wvht_ft": wh_ft,
    "actual_wvht_m": wh,
    "actual_dpd": dpd,
    "actual_mwd": mwd,
    "forecast_hs_ft": f_wh_ft,
    "forecast_hs_m": f_wh,
    "forecast_tp": f_tp,
    "forecast_dir": f_dir,
    "forecast_source": f_src
}

os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)
exists = os.path.exists(CSV_PATH)
with open(CSV_PATH, "a", newline="") as f:
    w = csv.DictWriter(f, fieldnames=row.keys())
    if not exists: w.writeheader()
    w.writerow(row)

print(f"Logged: actual {row['actual_wvht_ft']}ft @ {dpd}s | forecast {row['forecast_hs_ft']}ft via {f_src}")