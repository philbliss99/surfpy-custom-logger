"""
BACKFILL SURFPY - Option A - True Surfpy Backtest
Last 3 months, rolling window.

CORRECT IMPORTS FOR THIS VERSION OF SURFPY:
  from surfpy.location import Location
  from surfpy.wavemodel import WaveModel   # <-- NOT GFSWaveModel, that doesn't exist here
  # GFSApi in weatherapi.py is for weather, not waves

Run locally:
  python backfill_3month.py
"""
import csv
import os
import sys
from datetime import datetime, timezone, timedelta
import time
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from surfpy.location import Location
from surfpy.wavemodel import WaveModel

# --- CONFIG - ROLLING ---
END_DATE = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
START_DATE = END_DATE - timedelta(days=90)
LEADS = [0, 72, 168, 336]

BUOYS = {
    "44065": {"name": "rockaway", "lat": 40.369, "lon": -73.703, "tide_station": "8518750"},
    "46221": {"name": "santamonica", "lat": 33.852, "lon": -118.633, "tide_station": "9410840"},
    "46053": {"name": "santabarbara", "lat": 34.242, "lon": -119.773, "tide_station": "9411340"},
}

def safe(s):
    return str(s).replace(',', ';').replace('\n',' ').strip() if s is not None else ""

def fetch_ndbc_historical(buoy_id, target_date):
    try:
        year = target_date.year
        url = f"https://www.ndbc.noaa.gov/data/historical/stdmet/{buoy_id}h{year}.txt"
        r = requests.get(url, timeout=20)
        if r.status_code != 200:
            url = f"https://www.ndbc.noaa.gov/data/realtime2/{buoy_id}.txt"
            r = requests.get(url, timeout=10)
            r.raise_for_status()
        lines = r.text.strip().splitlines()
        for line in lines[2:]:
            parts = line.split()
            if len(parts) < 11:
                continue
            try:
                yy, mm, dd, hh = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
                if yy < 100:
                    yy += 2000
                if yy == target_date.year and mm == target_date.month and dd == target_date.day and hh == target_date.hour:
                    wvht = parts[8]
                    dpd = parts[9]
                    hs = float(wvht) if wvht != 'MM' else None
                    period = float(dpd) if dpd != 'MM' else None
                    return hs, period, "ok"
            except:
                continue
        return None, None, f"no match for {target_date}"
    except Exception as e:
        return None, None, safe(e)[:200]

def fetch_tide(station_id, dt_utc):
    try:
        begin_str = (dt_utc - timedelta(hours=1)).strftime("%Y%m%d %H:%M")
        end_str = (dt_utc + timedelta(hours=1)).strftime("%Y%m%d %H:%M")
        params = {"station": station_id, "product": "predictions", "datum": "MLLW", "units": "english", "time_zone": "gmt", "format": "json", "begin_date": begin_str, "end_date": end_str, "interval": "h"}
        r = requests.get("https://api.tidesandcurrents.noaa.gov/api/prod/datagetter", params=params, timeout=10)
        r.raise_for_status()
        j = r.json()
        preds = j.get('predictions', [])
        if not preds:
            return None, "no predictions"
        return float(preds[0]['v']), "ok"
    except Exception as e:
        return None, safe(e)[:200]

def fetch_wind_at_issued(lat, lon, dt_utc):
    try:
        date_str = dt_utc.strftime("%Y-%m-%d")
        url = f"https://archive-api.open-meteo.com/v1/archive?latitude={lat}&longitude={lon}&start_date={date_str}&end_date={date_str}&hourly=wind_speed_10m,wind_direction_10m,wind_gusts_10m&wind_speed_unit=kn&timezone=UTC"
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        j = r.json()
        hourly = j.get('hourly', {})
        times = hourly.get('time', [])
        target_key = dt_utc.strftime("%Y-%m-%dT%H:00")
        if target_key in times:
            idx = times.index(target_key)
            return hourly.get('wind_speed_10m', [])[idx], hourly.get('wind_direction_10m', [])[idx], hourly.get('wind_gusts_10m', [])[idx], "ok"
        return None, None, None, "time not found"
    except Exception as e:
        return None, None, None, safe(e)[:200]

def surfpy_forecast_for_issued(location, issued_dt, lead_hours):
    """
    True surfpy forecast using WaveModel.
    WaveModel in this surfpy version is the GFS wave model.
    """
    try:
        model = WaveModel(location, issued_dt)
        # Try common method names - surfpy versions differ
        forecasts = None
        for method_name in ["fetch_forecast", "fetch", "get_forecast", "fetch_data"]:
            if hasattr(model, method_name):
                try:
                    forecasts = getattr(model, method_name)()
                    break
                except Exception:
                    continue
        if forecasts is None:
            # If WaveModel fetches on init, it may store in .data / .forecast
            forecasts = getattr(model, 'data', None) or getattr(model, 'forecast', None) or getattr(model, 'forecasts', None)
        if not forecasts:
            return None, None, f"WaveModel has no fetch method. Methods: {[m for m in dir(model) if not m.startswith('_')]}"

        target_dt = issued_dt + timedelta(hours=lead_hours)
        best = None
        best_diff = None
        for fc in forecasts:
            fc_dt = getattr(fc, 'date', None) or getattr(fc, 'datetime', None) or getattr(fc, 'time', None)
            if fc_dt is None:
                continue
            if fc_dt.tzinfo is None:
                fc_dt = fc_dt.replace(tzinfo=timezone.utc)
            diff = abs((fc_dt - target_dt).total_seconds())
            if best_diff is None or diff < best_diff:
                best_diff = diff
                best = fc
        if best is None:
            return None, None, "no surfpy forecast found"
        hs_m = getattr(best, 'wave_height', None)
        if hs_m is None:
            hs_m = getattr(best, 'significant_wave_height', None)
            if hs_m is None:
                # Try swell / combined
                hs_m = getattr(best, 'swell_height', None)
        period = getattr(best, 'wave_period', None)
        if period is None:
            period = getattr(best, 'dominant_period', None)
            if period is None:
                period = getattr(best, 'period', None)
        return hs_m, period, "ok"
    except Exception as e:
        return None, None, safe(e)[:500]

def main():
    os.makedirs("data", exist_ok=True)
    print(f"Backfilling surfpy (WaveModel) - last 90 days: {START_DATE.date()} to {END_DATE.date()} - leads {LEADS}")

    for buoy_id, meta in BUOYS.items():
        lat, lon = meta['lat'], meta['lon']
        tide_station = meta['tide_station']
        csv_path = f"data/{buoy_id}_{meta['name']}_log.csv"
        fieldnames = ['issued_at_utc','target_time_utc','lead_hours','buoy_id','buoy_name','lat','lon',
                      'actual_hs_m','actual_hs_ft','actual_period_s',
                      'forecast_hs_m','forecast_hs_ft','forecast_period_s',
                      'wind_speed_kts','wind_dir_deg','wind_gust_kts',
                      'tide_ft','tide_station',
                      'forecast_source','tide_source',
                      'actual_status','forecast_status']
        f = open(csv_path, 'w', newline='', encoding='utf-8')
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        try:
            loc = Location(lat, lon)
        except Exception:
            loc = Location(lat, lon, meta['name'])

        issued = START_DATE
        while issued <= END_DATE:
            print(f"\n{meta['name']} issued {issued.strftime('%Y-%m-%d')}...")
            w_s, w_d, w_g, _ = fetch_wind_at_issued(lat, lon, issued)
            tide_ft, _ = fetch_tide(tide_station, issued)
            for lead in LEADS:
                target = issued + timedelta(hours=lead)
                f_h, f_p, f_status = surfpy_forecast_for_issued(loc, issued, lead)
                f_ft = round(f_h * 3.28084, 2) if f_h is not None else None
                a_m, a_period, a_status = fetch_ndbc_historical(buoy_id, target)
                a_ft = round(a_m * 3.28084, 2) if a_m is not None else None
                writer.writerow({
                    'issued_at_utc': issued.strftime("%Y-%m-%dT%H:00:00Z"),
                    'target_time_utc': target.strftime("%Y-%m-%dT%H:00:00Z"),
                    'lead_hours': lead,
                    'buoy_id': buoy_id,
                    'buoy_name': meta['name'],
                    'lat': lat, 'lon': lon,
                    'actual_hs_m': a_m if a_m is not None else "",
                    'actual_hs_ft': a_ft if a_ft is not None else "",
                    'actual_period_s': a_period if a_period is not None else "",
                    'forecast_hs_m': f_h if f_h is not None else "",
                    'forecast_hs_ft': f_ft if f_ft is not None else "",
                    'forecast_period_s': f_p if f_p is not None else "",
                    'wind_speed_kts': w_s if w_s is not None else "",
                    'wind_dir_deg': w_d if w_d is not None else "",
                    'wind_gust_kts': w_g if w_g is not None else "",
                    'tide_ft': tide_ft if tide_ft is not None else "",
                    'tide_station': tide_station,
                    'forecast_source': 'surfpy_wavemodel',
                    'tide_source': f"noaa_{tide_station}" if tide_ft is not None else "",
                    'actual_status': a_status,
                    'forecast_status': f_status if f_h is None else "ok"
                })
            f.flush()
            issued += timedelta(days=1)
            time.sleep(0.3)
        f.close()
        print(f"Done {meta['name']} -> {csv_path}")

if __name__ == "__main__":
    main()
