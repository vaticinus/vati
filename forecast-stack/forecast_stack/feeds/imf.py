"""IMF SDMX feed — keyless commodity-price collector (macro/commodity pillar).

The IMF publishes its Primary Commodity Price System (PCPS) through a public SDMX 2.1 service.
This collector reads it KEYLESS (no token, no auth header) and normalizes it to the engine's
dated-observation contract: every observation carries its REAL reporting period as `date`, the
real `value`, a `unit`, and a human `title`. Nothing is synthesized, backfilled, or forward-filled
— a month with no reported value simply isn't emitted.

Endpoint (probed 2026-06-11): the legacy `dataservices.imf.org/REST/SDMX_JSON.svc` host is dead/
SSL-broken; the live keyless service is the new gateway

    https://api.imf.org/external/sdmx/2.1/data/<AGENCY,FLOW,VERSION>/<KEY>?startPeriod=YYYY-MM

For PCPS the flow is `IMF.RES,PCPS,9.0.0`. The dimension order in the returned
StructureSpecificData is COUNTRY.INDICATOR.DATA_TRANSFORMATION.FREQUENCY, so a keyed series is e.g.
`G001.PALLFNF.INDEX.M` (G001 = the IMF world aggregate). The service ignores the
`Accept: application/vnd.sdmx.data+json` header and returns SDMX-ML either way, so we parse the XML
(<Series ...> attributes + <Obs TIME_PERIOD=.. OBS_VALUE=..>) with the stdlib — no SDMX client dep.

LEAK CLASS — LEADING. A commodity price is a forward-looking market clearing level: it reprices on
expectations of future supply/demand BEFORE the physical shortage or the macro print (CPI, IP,
trade balance) that the priced economic outcome resolves on. Energy/metal price breaks lead the
inflation and industrial-activity outcomes by months. (PCPS itself is a monthly average published
with a short lag, so it is a leading *signal* of the slower structural outcome, not a real-time tick.)

$0, keyless, stdlib-only. Representative series fetched below span energy, metals and the headline
all-commodity index — the inputs whose price is the binding-constraint tell for the energy/minerals
pillars.
"""

from __future__ import annotations

import json
from forecast_stack.config import DATA_DIR
import re
import time
import urllib.request
from datetime import date

UA = "forecast-stack (+https://github.com/vaticinus/vati)"
PCPS_FLOW = "IMF.RES,PCPS,9.0.0"
BASE = "https://api.imf.org/external/sdmx/2.1/data"
WORLD = "G001"            # IMF world-aggregate area for PCPS commodity indices
START = "2010-01"         # real history start; the service returns only what it actually has

# Full PCPS commodity basket: (indicator_code, human_title, sub_domain).
# All are the monthly INDEX transformation (2016=100), the cleanest cross-commodity comparable.
# Codes verified to resolve against IMF.RES,PCPS,9.0.0 at G001.<code>.INDEX.M (2026-06-20).
INDICATORS: list[tuple[str, str, str]] = [
    # --- aggregate indices (macro/inflation lead) ---
    ("PALLFNF", "IMF All-Commodity Price Index", "aggregate"),
    ("PNRG", "Energy Price Index", "aggregate"),
    ("PNFUEL", "Non-Fuel Price Index", "aggregate"),
    ("PMETA", "Base Metals Price Index", "aggregate"),
    ("PFANDB", "Food & Beverage Price Index", "aggregate"),
    ("PFOOD", "Food Price Index", "aggregate"),
    ("PBEVE", "Beverage Price Index", "aggregate"),
    ("PAGRI", "Agricultural Raw Materials Price Index", "aggregate"),
    ("PRAWM", "Industrial Inputs Price Index", "aggregate"),
    # --- energy ---
    ("POILBRE", "Crude Oil (Brent) Price Index", "energy"),
    ("POILDUB", "Crude Oil (Dubai) Price Index", "energy"),
    ("POILWTI", "Crude Oil (WTI) Price Index", "energy"),
    ("POILAPSP", "Crude Oil (avg spot) Price Index", "energy"),
    ("PNGASEU", "Natural Gas (Europe) Price Index", "energy"),
    ("PNGASUS", "Natural Gas (US Henry Hub) Price Index", "energy"),
    ("PNGASJP", "Natural Gas (Japan LNG) Price Index", "energy"),
    ("PCOALAU", "Coal (Australia) Price Index", "energy"),
    ("PCOALSA", "Coal (South Africa) Price Index", "energy"),
    ("PPROPANE", "Propane Price Index", "energy"),
    # --- metals ---
    ("PCOPP", "Copper Price Index", "metals"),
    ("PALUM", "Aluminum Price Index", "metals"),
    ("PIORECR", "Iron Ore Price Index", "metals"),
    ("PNICK", "Nickel Price Index", "metals"),
    ("PZINC", "Zinc Price Index", "metals"),
    ("PLEAD", "Lead Price Index", "metals"),
    ("PTIN", "Tin Price Index", "metals"),
    ("PURAN", "Uranium Price Index", "metals"),
    ("PCOBA", "Cobalt Price Index", "metals"),
    ("PLITH", "Lithium Price Index", "metals"),
    ("PGOLD", "Gold Price Index", "metals"),
    ("PSILVER", "Silver Price Index", "metals"),
    ("PPLAT", "Platinum Price Index", "metals"),
    # --- agriculture ---
    ("PWHEAMT", "Wheat Price Index", "agriculture"),
    ("PMAIZMT", "Corn (Maize) Price Index", "agriculture"),
    ("PSOYB", "Soybeans Price Index", "agriculture"),
    ("PRICENPQ", "Rice Price Index", "agriculture"),
    ("PSUGAISA", "Sugar (ISA) Price Index", "agriculture"),
    ("PSUGAUSA", "Sugar (US) Price Index", "agriculture"),
    ("PCOFFOTM", "Coffee (Other Milds) Price Index", "agriculture"),
    ("PCOFFROB", "Coffee (Robusta) Price Index", "agriculture"),
    ("PCOCO", "Cocoa Price Index", "agriculture"),
    ("PCOTTIND", "Cotton Price Index", "agriculture"),
    ("PPOIL", "Palm Oil Price Index", "agriculture"),
]
UNIT = "index (2016=100)"


def _fetch_series(indicator: str, *, retries: int = 2) -> list[tuple[str, float]] | None:
    """Fetch one PCPS world monthly INDEX series → [(time_period, value)]. None on persistent failure.

    Keyed SDMX query keeps the payload tiny. Returns the RAW reported (period, value) pairs — no gap
    filling, no synthesis."""
    key = f"{WORLD}.{indicator}.INDEX.M"
    url = f"{BASE}/{PCPS_FLOW}/{key}?startPeriod={START}"
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            raw = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "ignore")
        except Exception:  # noqa: BLE001 — network/SSL/parse: back off, retry, then None
            if attempt < retries:
                continue
            return None
        pairs = re.findall(r'<Obs[^>]*\bTIME_PERIOD="([^"]+)"[^>]*\bOBS_VALUE="([^"]+)"', raw)
        out: list[tuple[str, float]] = []
        for period, val in pairs:
            try:
                out.append((period, float(val)))
            except ValueError:
                continue
        return out or None
    return None


def _iso_date(period: str) -> str | None:
    """SDMX TIME_PERIOD → 'YYYY-MM-DD' (month-end, the point at which the monthly avg is knowable).

    Handles 'YYYY-Mmm' (monthly), 'YYYY-Qn' (quarter-end), 'YYYY' (year-end). Returns None if the
    period can't be parsed — we never guess a date for an observation."""
    m = re.fullmatch(r"(\d{4})-M(\d{2})", period)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
    else:
        q = re.fullmatch(r"(\d{4})-Q([1-4])", period)
        if q:
            y, mo = int(q.group(1)), int(q.group(2)) * 3
        elif re.fullmatch(r"\d{4}", period):
            y, mo = int(period), 12
        else:
            return None
    last_day = 28 if mo == 2 else (30 if mo in (4, 6, 9, 11) else 31)
    return date(y, mo, last_day).isoformat()


def collect() -> list[dict]:
    """Fetch the representative PCPS commodity-price series → list of normalized observation dicts.

    Each dict: {series_id, date:'YYYY-MM-DD', value:float, unit, title}. Real dates only; on a fetch
    failure for one indicator it is skipped (no fabrication), not faked."""
    obs: list[dict] = []
    for indicator, title, sub_domain in INDICATORS:
        series_id = f"imf_pcps_{indicator.lower()}"
        pairs = _fetch_series(indicator)
        if not pairs:
            print(f"  - skip {indicator} ({title}) — no data this fetch")
            time.sleep(0.4)
            continue
        kept = 0
        for period, value in pairs:
            iso = _iso_date(period)
            if iso is None:
                continue
            obs.append({
                "series_id": series_id,
                "date": iso,
                "value": value,
                "unit": UNIT,
                "metric": "commodity_price",
                "domain": "commodity",
                "sub_domain": sub_domain,
                "title": title,
            })
            kept += 1
        span = f"{pairs[0][0]}→{pairs[-1][0]}"
        print(f"  + {indicator:<8} {title:<40} {span}  {kept} obs")
        time.sleep(0.4)
    return obs


if __name__ == "__main__":
    out_path = DATA_DIR / "feeds" / "imf.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    observations = collect()
    observations.sort(key=lambda o: (o["series_id"], o["date"]))

    with open(out_path, "w", encoding="utf-8") as f:
        for o in observations:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")

    print(f"\nwrote {len(observations)} observations to {out_path}")
    print("first 3:")
    for o in observations[:3]:
        print("  ", json.dumps(o, ensure_ascii=False))
