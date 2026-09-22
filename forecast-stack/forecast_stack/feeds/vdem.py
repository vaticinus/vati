"""V-Dem (Varieties of Democracy) — keyless governance-index country-year collector.

A self-contained KEYLESS collector for Vati's data layer. The V-Dem Institute publishes its
core democracy indices (the v2x_* family) as a full country-year dataset. The V-Dem reference
distribution is an R `.RData` binary (vdeminstitute/vdemdata on GitHub) which is awkward to parse
without R; the same V-Dem estimates are republished as plain keyless CSV by Our World in Data's
grapher (one CSV per index), which is what we fetch here. Each grapher CSV is the V-Dem
`*_estimate_best` point estimate, country-year, sourced directly from V-Dem.

Three indices, mapped to their V-Dem variable names:
  • liberal-democracy-index      → v2x_libdem    (liberal democracy index)
  • electoral-democracy-index    → v2x_polyarchy (electoral democracy / polyarchy index)
  • participatory-democracy-index → v2x_partipdem (participatory democracy index)
All three are 0–1 continuous indices.

Leak discipline (matches forecast_stack/feeds/world_bank.py):
  • Every observation carries its REAL reference year — V-Dem is an ANNUAL country-year measure,
    so `date` = December 31 of the coded year (the point the year's regime state is established).
    Nothing is synthesized, backfilled, or interpolated: a row with a missing/non-numeric value is
    DROPPED, never filled. Only ground-truth coded points are written.
  • As a forecasting signal V-Dem is a LAG / CONFIRMATION channel: country experts code each year's
    democracy level AFTER that year, and the annual release lands months into the following year. It
    confirms a regime trajectory (democratic backsliding, autarky/liberalization) after the shift is
    underway and largely visible — a slow, authoritative structural baseline, not an early-warning.

normalized observation shape (one JSON object per jsonl line):
  {series_id, date:'YYYY-MM-DD', value:float, unit:str, title:str}

$0, keyless. Run directly:  uv run python forecast_stack/feeds/vdem.py
"""

from __future__ import annotations

import csv
import io
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from forecast_stack.config import DATA_DIR

UA = "forecast-stack (+https://github.com/vaticinus/vati)"
OWID_GRAPHER = "https://ourworldindata.org/grapher"
OUT_PATH = DATA_DIR / "feeds" / "vdem.jsonl"

# Three V-Dem core indices. Each: (owid grapher slug, owid short column, V-Dem variable name, title).
# The OWID short column is the `*_vdem__estimate_best` point estimate (V-Dem's own best estimate).
INDICES: list[dict] = [
    {
        "slug": "liberal-democracy-index",
        "column": "libdem_vdem__estimate_best",
        "vdem_var": "v2x_libdem",
        "title": "Liberal democracy index (V-Dem v2x_libdem)",
    },
    {
        "slug": "electoral-democracy-index",
        "column": "electdem_vdem__estimate_best",
        "vdem_var": "v2x_polyarchy",
        "title": "Electoral democracy index (V-Dem v2x_polyarchy)",
    },
    {
        "slug": "participatory-democracy-index",
        "column": "participdem_vdem__estimate_best",
        "vdem_var": "v2x_partipdem",
        "title": "Participatory democracy index (V-Dem v2x_partipdem)",
    },
]

# A small basket of representative countries (ISO-3 codes as OWID's `code` column uses them).
# Spread across regime types: an established democracy, two large powers on divergent trajectories,
# and a backsliding case — enough country-years per index to clear the >=30 bar comfortably.
COUNTRIES: list[tuple[str, str]] = [
    ("USA", "United States"),
    ("CHN", "China"),
    ("IND", "India"),
    ("HUN", "Hungary"),
    ("TUR", "Turkey"),
]

# Only keep recent country-years (V-Dem runs back to 1789; we want the structurally relevant window).
MIN_YEAR = 2000


def _fetch_csv(slug: str, column: str, *, retries: int = 2) -> list[dict] | None:
    """GET a keyless OWID grapher CSV → parsed rows (list of dicts). Returns None on persistent
    failure (never fakes). The CSV is `entity,code,year,<column>,owid_region`."""
    params = {"csvType": "full", "useColumnShortNames": "true"}
    url = f"{OWID_GRAPHER}/{urllib.parse.quote(slug)}.csv?{urllib.parse.urlencode(params)}"
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 keyless public endpoint
                text = resp.read().decode("utf-8", "replace")
            reader = csv.DictReader(io.StringIO(text))
            if column not in (reader.fieldnames or []):
                return None  # schema drift — refuse rather than fabricate
            return list(reader)
        except Exception:  # noqa: BLE001 — network/parse/throttle: back off, retry, then None
            if attempt < retries:
                time.sleep(1.0 * (attempt + 1))
                continue
            return None


def normalize(spec: dict, raw_rows: list[dict], iso3: str) -> list[dict]:
    """RAW OWID/V-Dem rows for one (index, country) → normalized Vati observations. `date` = Dec-31
    of the coded year. value cast to float; null/non-numeric DROPPED (never backfilled)."""
    series_id = f"vdem:{spec['vdem_var']}:{iso3}"
    col = spec["column"]
    out: list[dict] = []
    for r in raw_rows:
        if (r.get("code") or "").strip() != iso3:
            continue
        year = (r.get("year") or "").strip()
        if len(year) != 4 or not year.isdigit() or int(year) < MIN_YEAR:
            continue
        raw_val = (r.get(col) or "").strip()
        if raw_val in ("", "NA"):
            continue  # DROP missing — never interpolate (leak discipline)
        try:
            value = float(raw_val)
        except ValueError:
            continue
        out.append({
            "series_id": series_id,
            "date": f"{year}-12-31",          # REAL coded reference year (annual)
            "value": value,
            "unit": "index 0-1",
            "title": f"{spec['title']} — {iso3}",
        })
    out.sort(key=lambda o: o["date"])
    return out


def collect(*, log=print) -> list[dict]:
    """Fetch each index CSV once keyless, slice the basket countries out, normalize, write the jsonl.
    Returns the observations actually written. $0. Never fabricates: a failed fetch is skipped."""
    all_obs: list[dict] = []
    for spec in INDICES:
        raw = _fetch_csv(spec["slug"], spec["column"])
        if raw is None:
            log(f"  - skip {spec['vdem_var']} ({spec['slug']} unreachable / schema drift)")
            continue
        for iso3, name in COUNTRIES:
            obs = normalize(spec, raw, iso3)
            if not obs:
                log(f"  - skip {spec['vdem_var']} / {iso3} (no dated observations)")
                continue
            all_obs.extend(obs)
            log(f"  + {spec['vdem_var']:<14} {iso3}  "
                f"{obs[0]['date'][:4]}–{obs[-1]['date'][:4]}  {len(obs)} obs")
        time.sleep(0.3)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8") as f:
        for o in all_obs:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    log(f"\nwrote {len(all_obs)} observations → {OUT_PATH}")
    return all_obs


if __name__ == "__main__":
    observations = collect()
    if not observations:
        print("\nNO observations collected — OWID/V-Dem CSV unreachable this run (no data written).")
    else:
        print(f"\nfirst {min(3, len(observations))} observations:")
        for o in observations[:3]:
            print("  " + json.dumps(o, ensure_ascii=False))
        n_lines = sum(1 for _ in OUT_PATH.open(encoding="utf-8"))
        print(f"\njsonl line count: {n_lines}")
