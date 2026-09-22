"""Meteofrance temperature forecaster for the dbnomics dataset questions.

Two estimators, replacing the old climatology-exceedance model whose lagged
"due value" anchor produced confidently wrong forecasts in regime shifts
(2026-07-05 round: dbnomics Brier 0.74 on 48 resolved rows):

1. NWP (h <= NWP_MAX_DAYS, LIVE builds only): open-meteo forecast at the
   station's coordinates. p = Phi((F_res - F_due)/sigma(h)) where F_due is the
   model's day-0 daily-mean estimate and F_res the lead-h forecast. Point-in-time
   validation via the previous-runs archive on the 2026-06-21/07-05/07-19 rounds:
   Brier 0.139/0.108/0.173 vs 0.215/0.743/0.323 shipped. Leak discipline: this
   path only activates when `due` is today (UTC) — backtests never see it.

2. Anomaly-AR(1) climatology (all other horizons, and NWP fallback):
   X = clim(doy) + year_effect + AR(1) daily anomaly, all parameters from
   pre-due history only. P(X_res > X_due) has closed form; staleness of the
   last observation is modelled (phi^k decay) instead of treating the lagged
   value as the due-date value. Same-round validation at 30d:
   Brier 0.114/0.124 vs 0.312/0.323 shipped.

Outputs are natively calibrated probabilities — callers must NOT re-apply the
legacy dbnomics logit calibration (it was fit against the old model's biases).
"""
from __future__ import annotations

import json
import math
import re
import statistics
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from forecast_stack.config import DATA_DIR

CACHE = DATA_DIR / "forecastbench" / "cache"
CACHE.mkdir(parents=True, exist_ok=True)
UA = {"User-Agent": "Mozilla/5.0 (forecastbench-bot; research)"}

NWP_MAX_DAYS = 15          # open-meteo forecast_days=16 covers due(+0) .. due+15
NWP_SIGMA = (1.6, 0.18)    # sigma(h) = 1.6 + 0.18*h (deg C), lead-error of the daily-mean diff
NWP_CLAMP = (0.05, 0.95)   # NWP is sharp but never certain
ANOM_CLAMP = (0.03, 0.97)

# Meteo-France SYNOP station catalog (id -> lat, lon), from the public
# donnees-synop-essentielles-omm dataset (62 stations, incl. overseas).
STATIONS = {
    "07005": (50.1360, 1.8340), "07015": (50.5700, 3.0975), "07020": (49.7252, -1.9398), "07027": (49.1800, -0.4562),
    "07037": (49.3830, 1.1817), "07072": (49.2097, 4.1553), "07110": (48.4442, -4.4120), "07117": (48.8258, -3.4732),
    "07130": (48.0688, -1.7340), "07139": (48.4455, 0.1102), "07149": (48.7168, 2.3843), "07168": (48.3247, 4.0200),
    "07181": (48.5810, 5.9598), "07190": (48.5495, 7.6403), "07207": (47.2943, -3.2183), "07222": (47.1500, -1.6088),
    "07240": (47.4445, 0.7273), "07255": (47.0592, 2.3598), "07280": (47.2678, 5.0883), "07299": (47.6143, 7.5100),
    "07314": (46.0468, -1.4115), "07335": (46.5938, 0.3143), "07434": (45.8612, 1.1750), "07460": (45.7868, 3.1493),
    "07471": (45.0745, 3.7640), "07481": (45.7265, 5.0778), "07510": (44.8307, -0.6913), "07535": (44.7450, 1.3967),
    "07558": (44.1185, 3.0195), "07577": (44.5812, 4.7330), "07591": (44.5657, 6.5023), "07607": (43.9098, -0.5002),
    "07621": (43.1880, 0.0000), "07627": (43.0053, 1.1068), "07630": (43.6210, 1.3788), "07643": (43.5770, 3.9632),
    "07650": (43.4377, 5.2160), "07661": (43.0793, 5.9408), "07690": (43.6488, 7.2090), "07747": (42.7372, 2.8728),
    "07761": (41.9180, 8.7927), "07790": (42.5407, 9.4852), "61968": (-11.5827, 47.2897), "61970": (-17.0547, 42.7120),
    "61972": (-22.3442, 40.3407), "61976": (-15.8877, 54.5207), "61980": (-20.8925, 55.5287), "61996": (-37.7952, 77.5692),
    "61997": (-46.4325, 51.8567), "61998": (-49.3523, 70.2433), "67005": (-12.8055, 45.2828), "71805": (46.7663, -56.1792),
    "78890": (16.3350, -61.0040), "78894": (17.9015, -62.8522), "78897": (16.2640, -61.5163), "78922": (14.7745, -60.8753),
    "78925": (14.5953, -60.9957), "81401": (5.4855, -54.0317), "81405": (4.8223, -52.3653), "81408": (3.8907, -51.8047),
    "81415": (3.6402, -54.0283), "89642": (-66.6632, 140.0010),
}

_GEO_CACHE = CACHE / "weather_geocode.json"
_STATION_RE = re.compile(r"\.(\d{5})\.")
_NAME_RE = re.compile(r"French weather station at (.+?) will be higher")


def is_temperature_question(q: dict) -> bool:
    qid = str(q.get("id") or "")
    url = str(q.get("url") or "")
    return "TEMPERATURE" in qid.upper() or ("meteofrance" in url and "TEMPERATURE" in url.upper())


def _norm_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _get_json(url: str, timeout: int = 30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def station_coords(q: dict):
    """(lat, lon) for a meteofrance question: SYNOP catalog first, geocode fallback."""
    m = _STATION_RE.search(str(q.get("id") or ""))
    if m and m.group(1) in STATIONS:
        return STATIONS[m.group(1)]
    name_m = _NAME_RE.search(str(q.get("question") or ""))
    if not name_m:
        return None
    name = name_m.group(1).strip()
    try:
        cache = json.loads(_GEO_CACHE.read_text()) if _GEO_CACHE.exists() else {}
    except Exception:
        cache = {}
    if name in cache:
        v = cache[name]
        return tuple(v) if v else None
    cands = [name]
    base = re.sub(r"\s*(International\s+)?Airport\s*$", "", name).strip()
    cands.append(base)
    for sep in ("\u2013", "-", "/"):
        if sep in base:
            cands.extend(p.strip() for p in base.split(sep) if len(p.strip()) >= 3)
    out = None
    seen = set()
    for c in cands:
        if c.lower() in seen:
            continue
        seen.add(c.lower())
        try:
            j = _get_json("https://geocoding-api.open-meteo.com/v1/search?"
                          + urllib.parse.urlencode({"name": c, "count": 5, "format": "json"}))
            for r in j.get("results") or []:
                if r.get("country_code") in ("FR", "MC", "GP", "MQ", "RE", "GF", "YT",
                                             "NC", "PF", "PM", "BL", "MF", "TF", "WF"):
                    out = (r["latitude"], r["longitude"])
                    break
        except Exception:
            pass
        if out:
            break
        time.sleep(0.1)
    cache[name] = list(out) if out else None
    try:
        _GEO_CACHE.write_text(json.dumps(cache))
    except Exception:
        pass
    return out


# ---- live NWP path ----------------------------------------------------------

def _live_daily_means(lat: float, lon: float, due: date):
    """{date: forecast daily-mean temp} from due-day (day 0) out to due+15,
    cached per (station, due). Only meaningful when due == today (UTC)."""
    key = f"openmeteo_{lat:.3f}_{lon:.3f}_{due}"
    cf = CACHE / f"{key}.json"
    if cf.exists():
        try:
            return {datetime.strptime(k, "%Y-%m-%d").date(): v
                    for k, v in json.loads(cf.read_text()).items()}
        except Exception:
            pass
    j = _get_json("https://api.open-meteo.com/v1/forecast?"
                  + urllib.parse.urlencode({
                      "latitude": lat, "longitude": lon,
                      "daily": "temperature_2m_mean",
                      "past_days": 1, "forecast_days": 16, "timezone": "UTC"}))
    days = j["daily"]["time"]
    vals = j["daily"]["temperature_2m_mean"]
    out = {}
    for ds, v in zip(days, vals):
        if v is not None:
            out[datetime.strptime(ds, "%Y-%m-%d").date()] = float(v)
    try:
        cf.write_text(json.dumps({d.isoformat(): v for d, v in out.items()}))
    except Exception:
        pass
    return out


def p_nwp_live(lat: float, lon: float, due: date, res: date) -> float | None:
    """P(temp on res > temp on due) from the live forecast. None out of range."""
    h = (res - due).days
    if not (1 <= h <= NWP_MAX_DAYS):
        return None
    try:
        means = _live_daily_means(lat, lon, due)
    except Exception:
        return None
    f_due, f_res = means.get(due), means.get(res)
    if f_due is None or f_res is None:
        return None
    sigma = NWP_SIGMA[0] + NWP_SIGMA[1] * h
    p = _norm_cdf((f_res - f_due) / sigma)
    return min(NWP_CLAMP[1], max(NWP_CLAMP[0], p))


# ---- anomaly-AR(1) climatology ----------------------------------------------

def anom_stats(history, due: date):
    """Climatology + anomaly parameters from pre-due history only.

    Returns dict(climc, sigma_a, phi, sigma_y, trend, last_dt, last_anom) or None.
    """
    h = [(dt, v) for dt, v in history if dt <= due and v is not None]
    if len(h) < 730:
        return None
    by_doy = defaultdict(list)
    for dt, v in h:
        by_doy[dt.timetuple().tm_yday].append(v)
    clim_cache = {}

    def climc(doy):
        if doy not in clim_cache:
            pool = []
            for dd in range(-10, 11):
                d2 = (doy + dd - 1) % 366 + 1
                pool.extend(by_doy.get(d2, ()))
            clim_cache[doy] = statistics.fmean(pool) if len(pool) >= 30 else None
        return clim_cache[doy]

    anoms = []
    for dt, v in h:
        c = climc(dt.timetuple().tm_yday)
        if c is not None:
            anoms.append((dt, v - c))
    if len(anoms) < 400:
        return None
    vals = [a for _, a in anoms]
    sigma_a = statistics.pstdev(vals) or 1e-6
    num = den = 0.0
    for i in range(1, len(anoms)):
        if (anoms[i][0] - anoms[i - 1][0]).days == 1:
            num += anoms[i][1] * anoms[i - 1][1]
            den += anoms[i - 1][1] ** 2
    phi = min(0.95, max(0.0, num / den)) if den > 0 else 0.6
    by_year = defaultdict(list)
    for dt, a in anoms:
        by_year[dt.year].append(a)
    ymeans = [(y, statistics.fmean(v)) for y, v in sorted(by_year.items()) if len(v) >= 120]
    sigma_y = statistics.pstdev([m for _, m in ymeans]) if len(ymeans) >= 4 else 0.0
    trend = 0.0
    if len(ymeans) >= 6:
        ys = [y for y, _ in ymeans]
        ms = [m for _, m in ymeans]
        my, mm = statistics.fmean(ys), statistics.fmean(ms)
        sxx = sum((y - my) ** 2 for y in ys)
        if sxx > 0:
            trend = sum((y - my) * (m - mm) for y, m in ymeans) / sxx
    return {"climc": climc, "sigma_a": sigma_a, "phi": phi, "sigma_y": sigma_y,
            "trend": trend, "last_dt": anoms[-1][0], "last_anom": anoms[-1][1]}


def p_higher_anom(stats, due: date, res: date) -> float | None:
    """P(X_res > X_due) under clim + year-effect + AR(1) anomaly with lagged last obs."""
    if stats is None:
        return None
    climc = stats["climc"]
    c_due = climc(due.timetuple().tm_yday)
    c_res = climc(res.timetuple().tm_yday)
    if c_due is None or c_res is None:
        return None
    phi, sa, sy = stats["phi"], stats["sigma_a"], stats["sigma_y"]
    k = max(0, (due - stats["last_dt"]).days)
    h = max(1, (res - due).days)
    kc, hc = min(k, 400), min(h, 400)
    a_last = stats["last_anom"]
    phik, phih = phi ** kc, phi ** hc
    mu = (c_res - c_due) + stats["trend"] * (h / 365.25) \
        + (phi ** min(kc + hc, 400) - phik) * a_last
    var_e1 = sa * sa * (1 - phi ** (2 * kc))
    var_e2 = sa * sa * (1 - phi ** (2 * hc))
    var = (phih - 1) ** 2 * var_e1 + var_e2
    if res.year != due.year:
        var += 2 * sy * sy
    sd = math.sqrt(max(var, 1e-9))
    p = _norm_cdf(mu / sd)
    return min(ANOM_CLAMP[1], max(ANOM_CLAMP[0], p))


GH3 = ((-1.2247448713915890, 1 / 6), (0.0, 2 / 3), (1.2247448713915890, 1 / 6))


def p_higher_exceed(history, stats, due: date, res: date, window: int = 12) -> float | None:
    """Empirical exceedance of the res-DOY climatology pool vs a STALENESS-CORRECTED
    due-value estimate, integrated over its uncertainty (3-pt Gauss-Hermite).

    This keeps the shipped model's empirical-tail strength at 30d+ horizons but
    fixes its live failure mode: it used the lagged last observation as the due
    value (2026-07-05: heatwave last-obs -> dbnomics Brier 0.74). Under simulated
    3-day build lag on 445 resolved h>10d rows across seasons: 0.156 (this, with
    the legacy dbnomics calibration applied by the caller) vs 0.207 shipped vs
    0.167 anomaly-AR. Output expects the legacy dbnomics calibration ON TOP.
    """
    if stats is None:
        return None
    h = [(dt, v) for dt, v in history if dt <= due and v is not None]
    if len(h) < 730:
        return None
    doy = res.timetuple().tm_yday
    pool = []
    for dt, v in h:
        dd = abs(dt.timetuple().tm_yday - doy)
        if min(dd, 366 - dd) <= window:
            pool.append(v)
    if len(pool) < 15:
        return None
    climc = stats["climc"]
    c_due = climc(due.timetuple().tm_yday)
    c_last = climc(stats["last_dt"].timetuple().tm_yday)
    if c_due is None or c_last is None:
        x_due, sk = h[-1][1], 0.0
    else:
        k = max(0, (due - stats["last_dt"]).days)
        phi, sa = stats["phi"], stats["sigma_a"]
        x_due = c_due + (phi ** min(k, 400)) * stats["last_anom"]
        sk = sa * math.sqrt(max(0.0, 1 - phi ** (2 * min(k, 400))))
    n = len(pool)
    acc = 0.0
    for z, w in GH3:
        x = x_due + z * sk
        acc += w * (sum(1 for v in pool if v > x) + 1) / (n + 2)
    return acc

def forecast_temperature_question(q: dict, due: date, history, res_dates) -> dict:
    """{resolution_date_str: (p, needs_legacy_calibration)} for a meteofrance
    temperature question.

    Routing: live NWP for h <= NWP_MAX_DAYS when due == today (UTC); the
    staleness-corrected exceedance model otherwise (caller applies the legacy
    dbnomics calibration to it); anomaly-AR as the final in-module fallback.
    Returns {} only when every model lacks data (caller falls back to the
    legacy path)."""
    stats = anom_stats(history, due)
    coords = station_coords(q)
    live = due == datetime.now(timezone.utc).date()
    out = {}
    for rd in res_dates:
        try:
            res = datetime.strptime(str(rd)[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        h = (res - due).days
        if live and coords is not None and 1 <= h <= NWP_MAX_DAYS:
            p = p_nwp_live(coords[0], coords[1], due, res)
            if p is not None:
                out[rd] = (p, False)
                continue
        # h <= 10: the current anomaly and its decay dominate — anomaly-AR
        # (fresh-round 7d: 0.25/0.47/0.12 vs shipped 0.21/0.74/0.32). Live builds
        # rarely reach here for short h (NWP above). h > 10: empirical exceedance
        # with staleness-corrected due value (445 rows: 0.156 vs anomAR 0.167).
        first, second = ((p_higher_anom, p_higher_exceed) if h <= 10
                         else (p_higher_exceed, p_higher_anom))
        for fn in (first, second):
            p = (fn(stats, due, res) if fn is p_higher_anom
                 else fn(history, stats, due, res))
            if p is not None:
                out[rd] = (p, fn is p_higher_exceed)
                break
    return out
