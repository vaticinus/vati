"""FRED financial-conditions collector.

Keyless public FRED CSV series for rates, yield-curve shape, credit spreads, financial stress,
volatility, inflation expectations, mortgage rates, and liquidity/balance-sheet state. These are
timestamped market/macro state variables: useful as "what was already priced/known as of T" context
for forecasts. Missing FRED values (`.`) and future-dated rows are dropped.
"""

from __future__ import annotations

import csv
import io
import json
import math
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from forecast_stack.config import DATA_DIR
from typing import Any

UA = "forecast-stack (+https://github.com/vaticinus/vati)"
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"
OUT_PATH = DATA_DIR / "feeds" / "fred_financial.jsonl"
REQUEST_TIMEOUT_S = 30
MIN_REFRESH_FRACTION = 0.8

# Keyless FRED CSV series that exhibit regimes/changepoints (the firing kind). Each id verified
# HTTP 200 + non-empty CSV (2026-06-20). domain="financial" on every row.
SERIES: tuple[dict[str, str], ...] = (
    # --- policy / overnight rates ---
    {"id": "FEDFUNDS", "title": "Effective federal funds rate", "metric": "policy_rate", "unit": "percent"},
    {"id": "DFF", "title": "Effective federal funds rate (daily)", "metric": "policy_rate", "unit": "percent"},
    {"id": "SOFR", "title": "Secured Overnight Financing Rate", "metric": "overnight_rate", "unit": "percent"},
    # --- Treasury yield curve ---
    {"id": "DGS3MO", "title": "3-month Treasury yield", "metric": "treasury_yield", "unit": "percent"},
    {"id": "DGS1", "title": "1-year Treasury yield", "metric": "treasury_yield", "unit": "percent"},
    {"id": "DGS2", "title": "2-year Treasury yield", "metric": "treasury_yield", "unit": "percent"},
    {"id": "DGS5", "title": "5-year Treasury yield", "metric": "treasury_yield", "unit": "percent"},
    {"id": "DGS10", "title": "10-year Treasury yield", "metric": "treasury_yield", "unit": "percent"},
    {"id": "DGS30", "title": "30-year Treasury yield", "metric": "treasury_yield", "unit": "percent"},
    # --- term spreads ---
    {"id": "T10Y2Y", "title": "10-year minus 2-year Treasury spread", "metric": "yield_curve_spread", "unit": "percentage points"},
    {"id": "T10Y3M", "title": "10-year minus 3-month Treasury spread", "metric": "yield_curve_spread", "unit": "percentage points"},
    # --- credit spreads ---
    {"id": "BAMLC0A0CM", "title": "ICE BofA US Corporate Index option-adjusted spread", "metric": "credit_spread", "unit": "percent"},
    {"id": "BAMLH0A0HYM2", "title": "ICE BofA US High Yield Index option-adjusted spread", "metric": "credit_spread", "unit": "percent"},
    {"id": "BAA10Y", "title": "Moody's BAA corporate yield minus 10-year Treasury", "metric": "credit_spread", "unit": "percentage points"},
    {"id": "AAA", "title": "Moody's seasoned AAA corporate bond yield", "metric": "corporate_bond_yield", "unit": "percent"},
    {"id": "BAA", "title": "Moody's seasoned BAA corporate bond yield", "metric": "corporate_bond_yield", "unit": "percent"},
    # --- financial conditions / stress indices ---
    {"id": "NFCI", "title": "Chicago Fed National Financial Conditions Index", "metric": "financial_conditions_index", "unit": "index"},
    {"id": "ANFCI", "title": "Chicago Fed Adjusted National Financial Conditions Index", "metric": "financial_conditions_index", "unit": "index"},
    {"id": "STLFSI4", "title": "St. Louis Fed Financial Stress Index", "metric": "financial_stress_index", "unit": "index"},
    {"id": "VIXCLS", "title": "CBOE Volatility Index VIX", "metric": "equity_volatility", "unit": "index"},
    # --- inflation expectations ---
    {"id": "T5YIE", "title": "5-year breakeven inflation rate", "metric": "inflation_expectation", "unit": "percent"},
    {"id": "T10YIE", "title": "10-year breakeven inflation rate", "metric": "inflation_expectation", "unit": "percent"},
    {"id": "EXPINF2YR", "title": "Cleveland Fed 2-year expected inflation", "metric": "inflation_expectation", "unit": "percent"},
    {"id": "EXPINF5YR", "title": "Cleveland Fed 5-year expected inflation", "metric": "inflation_expectation", "unit": "percent"},
    # --- real rates ---
    {"id": "DFII5", "title": "5-year TIPS (real) yield", "metric": "real_rate", "unit": "percent"},
    {"id": "DFII10", "title": "10-year TIPS (real) yield", "metric": "real_rate", "unit": "percent"},
    # --- mortgage ---
    {"id": "MORTGAGE30US", "title": "30-year fixed mortgage rate", "metric": "mortgage_rate", "unit": "percent"},
    # --- liquidity / balance sheet / money ---
    {"id": "WALCL", "title": "Federal Reserve total assets", "metric": "central_bank_balance_sheet", "unit": "millions of dollars"},
    {"id": "M2SL", "title": "M2 money stock", "metric": "money_stock", "unit": "billions of dollars"},
    # --- dollar index ---
    {"id": "DTWEXBGS", "title": "Nominal broad US dollar index", "metric": "dollar_index", "unit": "index"},
    {"id": "DTWEXAFEGS", "title": "Nominal advanced-economies US dollar index", "metric": "dollar_index", "unit": "index"},
)


def _existing_line_count() -> int:
    if not OUT_PATH.exists():
        return 0
    with OUT_PATH.open(encoding="utf-8") as f:
        return sum(1 for _ in f)


def _write_jsonl_atomic(rows: list[dict[str, Any]]) -> None:
    tmp = OUT_PATH.with_suffix(".jsonl.tmp")
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tmp.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    tmp.replace(OUT_PATH)


def _fetch_csv(series_id: str) -> str:
    params = urllib.parse.urlencode({"id": series_id})
    req = urllib.request.Request(f"{FRED_CSV}?{params}", headers={"User-Agent": UA, "Accept": "text/csv"})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:  # noqa: S310 official public CSV
        return resp.read().decode("utf-8-sig", "replace")


def _to_float(raw: Any) -> float | None:
    text = str(raw if raw is not None else "").strip()
    if not text or text == ".":
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    if not math.isfinite(value):
        return None
    return value


def normalize_series(spec: dict[str, str], text: str, *, today: date | None = None) -> list[dict[str, Any]]:
    today = today or datetime.now(timezone.utc).date()
    reader = csv.DictReader(io.StringIO(text))
    rows: list[dict[str, Any]] = []
    value_field = spec["id"]
    for rec in reader:
        try:
            as_of = date.fromisoformat(str(rec.get("observation_date") or "")[:10])
        except ValueError:
            continue
        if as_of > today:
            continue
        value = _to_float(rec.get(value_field))
        if value is None:
            continue
        rows.append({
            "series_id": f"fred_financial:{spec['id']}:level",
            "date": as_of.isoformat(),
            "value": value,
            "unit": spec["unit"],
            "metric": spec["metric"],
            "domain": "financial",
            "title": f"FRED Financial Conditions - {spec['title']}",
            "fred_series_id": spec["id"],
        })
    return sorted(rows, key=lambda r: str(r["date"]))


def collect(*, log=print) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for spec in SERIES:
        try:
            text = _fetch_csv(spec["id"])
        except Exception as exc:  # noqa: BLE001 - public endpoint; preserve existing on partial refresh
            log(f"  - {spec['id']}: fetch failed: {exc}")
            continue
        parsed = normalize_series(spec, text)
        if parsed:
            log(f"  + {spec['id']:<12s} {len(parsed):6d} obs  {parsed[0]['date']}-{parsed[-1]['date']}  {spec['title']}")
            rows.extend(parsed)
        else:
            log(f"  - {spec['id']}: no parseable observations")

    existing = _existing_line_count()
    if not rows:
        log(f"\nno observations fetched; preserved existing {existing} rows at {OUT_PATH}")
        return []
    if existing and len(rows) < int(existing * MIN_REFRESH_FRACTION):
        log(
            f"\npartial refresh fetched {len(rows)} rows < "
            f"{MIN_REFRESH_FRACTION:.0%} of existing {existing}; preserved {OUT_PATH}"
        )
        return []
    _write_jsonl_atomic(sorted(rows, key=lambda r: (str(r["series_id"]), str(r["date"]))))
    log(f"\nwrote {len(rows)} observations -> {OUT_PATH}")
    return rows


if __name__ == "__main__":
    print("FRED financial conditions (keyless public CSV):")
    observations = collect()
    if not observations:
        print("\nNO observations collected - FRED unreachable/empty this run.")
    else:
        print(f"\nfirst {min(5, len(observations))} observations:")
        for o in observations[:5]:
            print("  " + json.dumps({k: o[k] for k in ("series_id", "date", "value", "unit", "title")}, ensure_ascii=False))
        print(f"\njsonl line count: {_existing_line_count()}")
