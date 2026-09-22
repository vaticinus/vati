"""World Bank Worldwide Governance Indicators (WGI) — keyless governance time-series collector.

A self-contained KEYLESS collector for Vati's data layer. The Worldwide Governance Indicators are
published THROUGH the same open World Bank Indicators API used by forecast_stack/feeds/world_bank.py
(https://api.worldbank.org/v2/country/<ISO>/indicator/<CODE>?format=json) — no API key, dated annual
observations returned as `[meta, rows]` JSON, paginated. This module fetches a small basket of major
reporters across the four WGI estimate indicators and writes a sample of >=30 REAL dated observations
to data/feeds/worldbank_wgi.jsonl.

WGI indicators (point estimates, ~ -2.5 weakest to +2.5 strongest governance). NOTE: the live WB v2
API serves WGI under source 3 with `GOV_WGI_`-prefixed codes (the bare PV.EST/GE.EST/RL.EST/CC.EST
codes are archived and 404); the prefixed codes below were verified against the catalog:
  • GOV_WGI_PV.EST  — Political Stability and Absence of Violence/Terrorism
  • GOV_WGI_GE.EST  — Government Effectiveness
  • GOV_WGI_RL.EST  — Rule of Law
  • GOV_WGI_CC.EST  — Control of Corruption

Leak discipline (matches forecast_stack/feeds/world_bank.py):
  • Every observation carries its REAL reporting date — WGI are annual, so `date` = December 31 of the
    indicator's reference year (the point in time the year's value is knowable). Nothing is synthesized,
    backfilled, or interpolated: a null `value` row is DROPPED, never filled.
  • WGI are constructed by aggregating dozens of underlying perception/expert/survey sources for a given
    reference year and are PUBLISHED WITH A LONG LAG (typically released ~9-12 months after the reference
    year, and the whole back-series is re-estimated each vintage). As a forecasting signal this is a
    LAG / CONFIRMATION channel: it confirms that an institutional-quality / political-stability regime
    has shifted AFTER it has already happened and been perceived — it does not lead the shift. It grounds
    the governance/politics pillar as a slow, authoritative baseline / kill-metric, not an early-warning.

normalized observation shape (one JSON object per jsonl line):
  {series_id, date:'YYYY-MM-DD', value:float, unit:str, title:str}

$0, keyless. Run directly:  uv run python forecast_stack/feeds/worldbank_wgi.py
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from forecast_stack.config import DATA_DIR

UA = "forecast-stack (+https://github.com/vaticinus/vati)"
WB_BASE = "https://api.worldbank.org/v2"
OUT_PATH = DATA_DIR / "feeds" / "worldbank_wgi.jsonl"

# The four WGI point-estimate indicators. Each is served keyless through the standard WB v2 API.
INDICATORS: list[dict] = [
    {
        "code": "GOV_WGI_PV.EST",
        "unit": "estimate (-2.5 to 2.5)",
        "title": "Political Stability and Absence of Violence/Terrorism: Estimate",
        "domain": "governance",
    },
    {
        "code": "GOV_WGI_GE.EST",
        "unit": "estimate (-2.5 to 2.5)",
        "title": "Government Effectiveness: Estimate",
        "domain": "governance",
    },
    {
        "code": "GOV_WGI_RL.EST",
        "unit": "estimate (-2.5 to 2.5)",
        "title": "Rule of Law: Estimate",
        "domain": "governance",
    },
    {
        "code": "GOV_WGI_CC.EST",
        "unit": "estimate (-2.5 to 2.5)",
        "title": "Control of Corruption: Estimate",
        "domain": "governance",
    },
]

# A small basket of major reporters. USA + CHN are the two largest economies; RUS is a structurally
# interesting governance/stability case. ISO-3 codes as the API expects them.
REPORTERS: list[tuple[str, str]] = [
    ("USA", "United States"),
    ("CHN", "China"),
    ("RUS", "Russian Federation"),
]

PER_PAGE = 1000  # full WGI annual history (1996–present) fits in one page


def _fetch_json(url: str, *, retries: int = 2):
    """GET a keyless World Bank API URL → parsed JSON. Returns None on persistent failure (never fakes)."""
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=40) as resp:  # noqa: S310 keyless public endpoint
                return json.loads(resp.read().decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001 — network/parse/throttle: back off, retry, then None
            if attempt < retries:
                time.sleep(1.0 * (attempt + 1))
                continue
            return None


def fetch_indicator(code: str, iso3: str, *, retries: int = 2) -> list[dict]:
    """Fetch one (indicator, reporter) series, paginated, → list of RAW World Bank rows with a
    non-null value. The API returns `[meta, rows]`; an invalid/archived code returns `[{"message":...}]`."""
    rows: list[dict] = []
    page = 1
    while True:
        params = {"format": "json", "per_page": PER_PAGE, "page": page}
        url = f"{WB_BASE}/country/{urllib.parse.quote(iso3)}/indicator/{urllib.parse.quote(code)}?{urllib.parse.urlencode(params)}"
        data = _fetch_json(url, retries=retries)
        # Valid payload is a 2-element list [meta, data]; anything else (error envelope / None) → stop.
        if not isinstance(data, list) or len(data) != 2 or not isinstance(data[1], list):
            break
        meta, page_rows = data
        for r in page_rows:
            if r.get("value") is not None:  # DROP nulls — never backfill/interpolate (leak discipline)
                rows.append(r)
        total_pages = (meta or {}).get("pages", 1)
        if page >= total_pages:
            break
        page += 1
        time.sleep(0.2)
    return rows


def normalize(code: str, iso3: str, spec: dict, raw_rows: list[dict]) -> list[dict]:
    """RAW World Bank rows → normalized Vati observations. `date` = Dec-31 of the reference year (the
    point in time the annual value is knowable). value cast to float; unit/title from the spec."""
    series_id = f"worldbank_wgi:{code}:{iso3}"
    out: list[dict] = []
    for r in raw_rows:
        year = (r.get("date") or "").strip()
        if len(year) != 4 or not year.isdigit():
            continue
        try:
            value = float(r["value"])
        except (KeyError, TypeError, ValueError):
            continue
        out.append({
            "series_id": series_id,
            "date": f"{year}-12-31",          # REAL reference year, reported point-in-time (annual)
            "value": value,
            "unit": spec["unit"],
            "title": f"{spec['title']} — {iso3}",
        })
    # chronological order, latest last
    out.sort(key=lambda o: o["date"])
    return out


def collect(*, log=print) -> list[dict]:
    """Fetch all (indicator × reporter) series keyless, normalize, write the jsonl. Returns the
    list of observations actually written. $0. Never fabricates: a series that fails to fetch is
    logged and skipped, not filled."""
    all_obs: list[dict] = []
    for spec in INDICATORS:
        code = spec["code"]
        for iso3, name in REPORTERS:
            raw = fetch_indicator(code, iso3)
            obs = normalize(code, iso3, spec, raw)
            if not obs:
                log(f"  - skip {code} / {iso3} (no dated observations returned)")
                continue
            all_obs.extend(obs)
            log(f"  + {spec['domain']:<10} {code:<8} {iso3}  "
                f"{obs[0]['date'][:4]}–{obs[-1]['date'][:4]}  {len(obs)} obs")
            time.sleep(0.2)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8") as f:
        for o in all_obs:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    log(f"\nwrote {len(all_obs)} observations → {OUT_PATH}")
    return all_obs


if __name__ == "__main__":
    observations = collect()
    if not observations:
        print("\nNO observations collected — World Bank API unreachable this run (no data written).")
    else:
        print(f"\nfirst {min(3, len(observations))} observations:")
        for o in observations[:3]:
            print("  " + json.dumps(o, ensure_ascii=False))
        # report the true count straight from the written file
        n_lines = sum(1 for _ in OUT_PATH.open(encoding="utf-8"))
        print(f"\njsonl line count: {n_lines}")
