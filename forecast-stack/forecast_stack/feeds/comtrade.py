"""UN Comtrade preview API — keyless critical-commodity import-dependency collector.

A self-contained KEYLESS collector for Vati's data layer. UN Comtrade's modern public *preview*
endpoint (https://comtradeapi.un.org/public/v1/preview/C/A/HS) is open — no subscription key — and
returns bilateral merchandise trade records as `{"data":[...]}` JSON. This module fetches the annual
IMPORT value (flowCode=M) of a small basket of *critical* commodities (rare/critical metals, PV /
semiconductor diodes, radioactive/nuclear materials) for a basket of major reporters, taking the
World total (partnerCode=0). The point is a SUPPLY-DEPENDENCY signal: how much a reporter spends
importing an inelastic input, and — across reporters — how concentrated that dependency is (an HHI of
reporter import shares is the natural derived dependency metric on top of these series).

Why partner=0 needs careful filtering: the preview slice for a single reporter can return many rows
for the same (reporter, period, cmdCode, partnerCode=0). They are NOT duplicates — they are broken out
by the SECOND partner (`partner2Code`), customs procedure (`customsCode`) and mode-of-transport
(`motCode`). The single TRUE "all-partners total" line is the one with
`partner2Code==0 & customsCode=='C00' & motCode==0 & isAggregate==True`. We keep only that row, so a
reporter/period/commodity maps to exactly one observation (verified against USA/CHN/DEU 2022).

Leak discipline (matches forecast_stack/feeds/world_bank.py):
  • Every observation carries its REAL reporting date. Comtrade annual figures are knowable only after
    the reference year closes, so `date` = December 31 of the reference year. Nothing is synthesized,
    backfilled, or interpolated: a row with a missing/zero/null trade value is DROPPED, never filled.
  • Annual customs data is PUBLISHED WITH A LAG (reporters file months-to-a-year after year-end; some
    cells are estimated, flagged `isAggregate/legacyEstimationFlag` upstream). As a forecasting signal
    this is a LAG / CONFIRMATION channel: it confirms a dependency shift (a reporter ramping imports of
    a critical input, a concentration move) AFTER it has happened. The forward edge is the DERIVED
    concentration (HHI of import shares across reporters), not the raw level.

normalized observation shape (one JSON object per jsonl line):
  {series_id, date:'YYYY-MM-DD', value:float (trade USD), unit:'USD', title:str}

$0, keyless. Run directly:  uv run python forecast_stack/feeds/comtrade.py
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from forecast_stack.config import DATA_DIR

UA = "forecast-stack (+https://github.com/vaticinus/vati)"
CT_BASE = "https://comtradeapi.un.org/public/v1/preview/C/A/HS"
OUT_PATH = DATA_DIR / "feeds" / "comtrade.jsonl"
MIN_REFRESH_FRACTION = 0.8

# Critical commodities (4-digit HS): inelastic supply-dependency inputs. Each: (hs, title).
COMMODITIES: list[dict] = [
    {"hs": "2805", "title": "Alkali/rare-earth metals (rare & critical metals)"},
    {"hs": "8541", "title": "Semiconductor diodes / photovoltaic cells"},
    {"hs": "2844", "title": "Radioactive / nuclear materials"},
]

# Major reporters by Comtrade M49 numeric code. The cross-reporter spread of import value is the
# dependency-concentration signal. (reporter-code, name).
REPORTERS: list[tuple[str, str]] = [
    ("842", "United States"),
    ("156", "China"),
    ("276", "Germany"),
    ("392", "Japan"),
    ("410", "South Korea"),
]

# Recent reference years. Comtrade preview accepts comma-joined periods in one call.
PERIODS: list[str] = ["2019", "2020", "2021", "2022", "2023"]


def _row_key(row: dict) -> tuple[str, str]:
    return str(row.get("series_id") or ""), str(row.get("date") or "")


def _existing_rows() -> list[dict]:
    if not OUT_PATH.exists():
        return []
    rows: list[dict] = []
    with OUT_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if _row_key(row) != ("", ""):
                rows.append(row)
    return rows


def _merge_rows(old: list[dict], new: list[dict]) -> list[dict]:
    merged = {_row_key(row): row for row in old if _row_key(row) != ("", "")}
    for row in new:
        key = _row_key(row)
        if key != ("", ""):
            merged[key] = row
    return sorted(merged.values(), key=lambda r: (_row_key(r)[0], _row_key(r)[1]))


def _write_rows(rows: list[dict]) -> None:
    tmp = OUT_PATH.with_suffix(".jsonl.tmp")
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tmp.open("w", encoding="utf-8") as f:
        for o in rows:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    tmp.replace(OUT_PATH)


def _fetch_json(url: str, *, retries: int = 3):
    """GET a keyless Comtrade preview URL → parsed JSON. Comtrade throttles HARD (409/429); back off
    generously and retry. Returns None on persistent failure (never fakes)."""
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 keyless public endpoint
                return json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:  # noqa: PERF203
            # 409/429 = rate-limited; 5xx = transient. Back off longer each attempt, then give up.
            if e.code in (409, 429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(2.0 * (attempt + 1))
                continue
            return None
        except Exception:  # noqa: BLE001 — network/parse: back off, retry, then None
            if attempt < retries:
                time.sleep(2.0 * (attempt + 1))
                continue
            return None


def _is_total_row(r: dict) -> bool:
    """The single TRUE all-partners total line for a (reporter, period, cmdCode). The preview splits
    partner=0 into partner2 / customs / mode-of-transport breakouts; only this combination is the
    unduplicated grand total (verified USA/CHN/DEU 2022)."""
    return (
        r.get("partnerCode") == 0
        and r.get("partner2Code") == 0
        and r.get("customsCode") == "C00"
        and r.get("motCode") == 0
        and bool(r.get("isAggregate"))
    )


def fetch_commodity(hs: str, reporter_code: str, *, retries: int = 3) -> list[dict]:
    """Fetch one (commodity, reporter) series across all PERIODS in a single keyless call → list of
    RAW Comtrade total rows (one per period). Empty on failure/throttle (never fakes)."""
    params = {
        "reporterCode": reporter_code,
        "period": ",".join(PERIODS),
        "cmdCode": hs,
        "flowCode": "M",          # M = imports (the dependency side)
        "partnerCode": "0",       # 0 = World (all partners), then de-duplicated by _is_total_row
    }
    url = f"{CT_BASE}?{urllib.parse.urlencode(params)}"
    data = _fetch_json(url, retries=retries)
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        return []
    return [r for r in data["data"] if _is_total_row(r)]


def normalize(hs: str, reporter_code: str, spec: dict, name: str, raw_rows: list[dict]) -> list[dict]:
    """RAW Comtrade total rows → normalized Vati observations. `date` = Dec-31 of the reference year.
    value = primaryValue (trade USD) cast to float; a missing/zero/null value is DROPPED."""
    series_id = f"comtrade:{hs}:{reporter_code}"
    out: list[dict] = []
    for r in raw_rows:
        year = str(r.get("refYear") or r.get("period") or "").strip()
        if len(year) != 4 or not year.isdigit():
            continue
        try:
            value = float(r["primaryValue"])
        except (KeyError, TypeError, ValueError):
            continue
        if value <= 0:  # DROP zero/null — never fabricate a dependency where none is reported
            continue
        out.append({
            "series_id": series_id,
            "date": f"{year}-12-31",          # REAL reference year (annual customs total)
            "value": value,
            "unit": "USD",
            "title": f"{spec['title']} imports — {name}",
        })
    out.sort(key=lambda o: o["date"])
    return out


def collect(*, log=print) -> list[dict]:
    """Fetch all (commodity × reporter) import series keyless, normalize, write the jsonl. Returns the
    list of observations actually written. $0. Comtrade throttles hard, so calls are paced (~1.5s) and
    a series that fails/throttles is logged and skipped, not filled."""
    all_obs: list[dict] = []
    for spec in COMMODITIES:
        hs = spec["hs"]
        for reporter_code, name in REPORTERS:
            raw = fetch_commodity(hs, reporter_code)
            obs = normalize(hs, reporter_code, spec, name, raw)
            if not obs:
                log(f"  - skip HS{hs} / {name} (no total rows — empty or throttled)")
            else:
                all_obs.extend(obs)
                log(f"  + HS{hs:<5} {name:<14} {obs[0]['date'][:4]}–{obs[-1]['date'][:4]}  "
                    f"{len(obs)} obs")
            time.sleep(1.5)  # Comtrade preview throttles hard — pace between calls

    existing = _existing_rows()
    if not all_obs:
        if existing:
            log(f"\nno observations fetched; preserved existing {len(existing)} rows at {OUT_PATH}")
        return []
    if existing and len(all_obs) < MIN_REFRESH_FRACTION * len(existing):
        log(
            f"\npartial Comtrade refresh fetched {len(all_obs)} rows, below "
            f"{MIN_REFRESH_FRACTION:.0%} of existing {len(existing)}; preserved {OUT_PATH}"
        )
        return []

    merged = _merge_rows(existing, all_obs)
    _write_rows(merged)
    retained = len(merged) - len(all_obs)
    suffix = f" ({retained} prior rows retained)" if retained else ""
    log(f"\nwrote {len(merged)} observations → {OUT_PATH}{suffix}")
    return merged


if __name__ == "__main__":
    observations = collect()
    if not observations:
        print("\nNO observations collected — Comtrade preview unreachable/throttled this run.")
    else:
        print(f"\nfirst {min(3, len(observations))} observations:")
        for o in observations[:3]:
            print("  " + json.dumps(o, ensure_ascii=False))
        n_lines = sum(1 for _ in OUT_PATH.open(encoding="utf-8"))
        print(f"\njsonl line count: {n_lines}")
