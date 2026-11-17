import csv, os, requests
from datetime import datetime, timezone, timedelta

BUOYS = [
    {"id": "44065", "name": "rockaway", "lat": 40.369, "lon": -73.703, "ndbc": "44065", "tide_station": "8516945"},
    {"id": "46221", "name": "santamonica", "lat": 33.85, "lon": -118.64, "ndbc": "46221", "tide_station": "9410840"},
    {"id": "46053", "name": "santabarbara", "lat": 34.242, "lon": -119.773, "ndbc": "46053", "tide_station": "9411340"},
]

LEADS = [0, 6, 24, 48]

def safe(s):
    return str(s).replace(',', ';').replace('\n',' ') if s else ""

def m_to_ft(m):
    return round(m * 3.28084, 2) if m is not None else ""

def fetch_ndbc_realtime(buoy_id):
    url = f"https://www.ndbc.noaa.gov/data/realtime2/{buoy_id}.txt"
    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        lines = r.text.strip().splitlines()
        if len(lines) < 3:
            return None, None, "no data lines"
        header = lines[0].replace('#','').split()
        data = lines[2].split()
        if len(data) < len(header):
            return None, None, f"short data line {data}"
        d = dict(zip(header, data))
        wvht = d.get("WVHT")
        if wvht in (None, "MM"):
            return None, None, f"WVHT={wvht}"
        dpd = d.get("DPD")
        if dpd == "MM":
            dpd = None
        return float(wvht), float(dpd) if dpd else None, "ok"
    except Exception as e:
        return None, None, str(e)

def fetch_openmeteo_forecasts(lat, lon):
    url = (f"https://marine-api.open-meteo.com/v1/marine?latitude={lat}&longitude={lon}"
           f"&hourly=wave_height,wave_period&forecast_days=3&timezone=UTC")
    try:
        r = requests.get(url, timeout=25)
        r.raise_for_status()
        j = r.json()
        times = j.get("hourly", {}).get("time", [])
        wh = j.get("hourly", {}).get("wave_height", [])
        wp = j.get("hourly", {}).get("wave_period", [])
        if not times:
            return None, "empty hourly"
        out = []
        for t, h, p in zip(times, wh, wp):
            out.append((t, h, p))
        return out, "ok"
    except Exception as e:
        return None, str(e)

def fetch_wind_and_tide(lat, lon, tide_station, issued_at):
    """Fetch wind + tide at issued_at time"""
    wind_speed_kts = ""
    wind_dir_deg = ""
    tide_ft = ""
    
    # Wind from Open-Meteo weather API
    try:
        # Get wind for the issued hour
        date_str = issued_at.strftime("%Y-%m-%d")
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&hourly=wind_speed_10m,wind_direction_10m&wind_speed_unit=kn&timezone=UTC&start_date={date_str}&end_date={date_str}"
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        j = r.json()
        times = j.get("hourly", {}).get("time", [])
        ws = j.get("hourly", {}).get("wind_speed_10m", [])
        wd = j.get("hourly", {}).get("wind_direction_10m", [])
        target_str = issued_at.strftime("%Y-%m-%dT%H:00")
        for t, s, d in zip(times, ws, wd):
            if t == target_str:
                wind_speed_kts = round(s,1) if s is not None else ""
                wind_dir_deg = round(d) if d is not None else ""
                break
    except Exception as e:
        print(f"  wind fetch failed: {e}")
    
    # Tide from NOAA CO-OPS
    try:
        # NOAA needs begin/end
        begin = issued_at.strftime("%Y%m%d %H:%M")
        end = (issued_at + timedelta(hours=1)).strftime("%Y%m%d %H:%M")
        url = f"https://api.tidesandcurrents.noaa.gov/api/prod/datagetter?product=predictions&application=surfpy&begin_date={begin}&end_date={end}&datum=MLLW&station={tide_station}&time_zone=gmt&units=english&interval=h&format=json"
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        preds = r.json().get("predictions", [])
        if preds:
            tide_ft = float(preds[0]['v'])
    except Exception as e:
        print(f"  tide fetch failed: {e}")
    
    return wind_speed_kts, wind_dir_deg, tide_ft

os.makedirs("data", exist_ok=True)
issued_at = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)

# NEW HEADER with wind + tide
header = ["issued_at_utc","target_time_utc","lead_hours","buoy_id","buoy_name","lat","lon",
          "actual_hs_m","actual_hs_ft","actual_period_s",
          "forecast_hs_m","forecast_hs_ft","forecast_period_s",
          "wind_speed_kts","wind_dir_deg","tide_ft",
          "forecast_source","actual_status","forecast_status"]

for b in BUOYS:
    buoy_id = b["id"]
    name = b["name"]
    lat = b["lat"]
    lon = b["lon"]
    ndbc_id = b["ndbc"]
    tide_station = b["tide_station"]

    print(f"\n--- {buoy_id} ({name}) ---")
    actual_m, actual_period, actual_status = fetch_ndbc_realtime(ndbc_id)
    print(f" actual: {actual_m}m / {actual_status}")

    forecasts, fc_status = fetch_openmeteo_forecasts(lat, lon)
    if forecasts is None:
        print(f" forecast fail: {fc_status}")
        continue
    print(f" forecast: {len(forecasts)} hours ok")

    # Fetch wind + tide ONCE per buoy at issued_at
    wind_speed_kts, wind_dir_deg, tide_ft = fetch_wind_and_tide(lat, lon, tide_station, issued_at)
    print(f" wind: {wind_speed_kts}kts @ {wind_dir_deg}deg, tide: {tide_ft}ft")

    csv_path = f"data/{buoy_id}_{name}_log.csv"
    write_header = not os.path.exists(csv_path)

    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=header)
        if write_header:
            w.writeheader()

        for lead in LEADS:
            target = issued_at + timedelta(hours=lead)
            target_str = target.strftime("%Y-%m-%dT%H:00")

            match = next((x for x in forecasts if x[0] == target_str), None)
            if not match:
                def diff_iso(iso):
                    try:
                        dt = datetime.fromisoformat(iso.replace("Z","")).replace(tzinfo=timezone.utc)
                        return abs((dt - target).total_seconds())
                    except:
                        return 1e9
                match = min(forecasts, key=lambda x: diff_iso(x[0]))
                if diff_iso(match[0]) > 7200:
                    print(f"  no forecast near {target_str}, skipping lead {lead}")
                    continue

            f_time, f_h, f_p = match
            is_now = (lead == 0)

            row = {
                "issued_at_utc": issued_at.isoformat().replace("+00:00","Z"),
                "target_time_utc": f_time + ":00Z" if not f_time.endswith("Z") else f_time,
                "lead_hours": lead,
                "buoy_id": buoy_id,
                "buoy_name": name,
                "lat": lat,
                "lon": lon,
                "actual_hs_m": actual_m if is_now and actual_m is not None else "",
                "actual_hs_ft": m_to_ft(actual_m) if is_now and actual_m is not None else "",
                "actual_period_s": actual_period if is_now and actual_period is not None else "",
                "forecast_hs_m": f_h if f_h is not None else "",
                "forecast_hs_ft": m_to_ft(f_h) if f_h is not None else "",
                "forecast_period_s": f_p if f_p is not None else "",
                "wind_speed_kts": wind_speed_kts,
                "wind_dir_deg": wind_dir_deg,
                "tide_ft": tide_ft,
                "forecast_source": "openmeteo",
                "actual_status": actual_status if is_now else "",
                "forecast_status": fc_status if f_h is None else "ok",
            }
            w.writerow(row)
            print(f"  Logged lead={lead}h target={row['target_time_utc']} actual={row['actual_hs_ft']}ft forecast={row['forecast_hs_ft']}ft")

print("\nDone - Schema v3 with wind + tide")
