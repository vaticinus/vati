"""ECB euro foreign-exchange reference rates collector.

Official keyless ECB historical CSV, distributed as a small ZIP. Each row is a real ECB reference
date and each value is units of the quoted currency per 1 euro. Missing ``N/A`` cells are dropped;
future-dated rows are capped defensively.
"""

from __future__ import annotations

import csv
import io
import json
import math
import urllib.request
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from forecast_stack.config import DATA_DIR
from typing import Any

UA = "forecast-stack (+https://github.com/vaticinus/vati)"
ECB_FX_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip"
OUT_PATH = DATA_DIR / "feeds" / "ecb_fx.jsonl"
REQUEST_TIMEOUT_S = 30
MAX_CURRENCY_LAG_DAYS = 10


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _fetch_zip() -> bytes:
    req = urllib.request.Request(ECB_FX_URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:  # noqa: S310 official ECB file
        return resp.read()


def _write_jsonl_atomic(rows: list[dict[str, Any]]) -> None:
    tmp = OUT_PATH.with_suffix(".jsonl.tmp")
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tmp.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    tmp.replace(OUT_PATH)


def _csv_text(raw_zip: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(raw_zip)) as zf:
        names = [n for n in zf.namelist() if n.endswith(".csv")]
        if not names:
            raise ValueError("ECB FX ZIP did not contain a CSV file")
        return zf.read(names[0]).decode("utf-8-sig", "replace")


def normalize_csv(
    text: str,
    *,
    today: date | None = None,
    max_currency_lag_days: int = MAX_CURRENCY_LAG_DAYS,
) -> list[dict[str, Any]]:
    today = today or _today()
    out: list[dict[str, Any]] = []
    latest_by_currency: dict[str, date] = {}
    years_by_currency: dict[str, set[int]] = {}
    reader = csv.DictReader(io.StringIO(text))
    for raw in reader:
        try:
            d = date.fromisoformat(str(raw.get("Date") or "")[:10])
        except ValueError:
            continue
        if d > today:
            continue
        for currency, value_raw in raw.items():
            if not currency or currency == "Date" or value_raw in (None, "", "N/A"):
                continue
            try:
                value = float(value_raw)
            except ValueError:
                continue
            if not math.isfinite(value):
                continue
            latest_by_currency[currency] = max(latest_by_currency.get(currency, d), d)
            years_by_currency.setdefault(currency, set()).add(d.year)
            out.append(
                {
                    "series_id": f"ecb_fx:{currency}:eur_reference_rate",
                    "date": d.isoformat(),
                    "value": value,
                    "unit": f"{currency} per EUR",
                    "metric": "fx_reference_rate",
                    "title": f"ECB FX - {currency} per EUR reference rate",
                    "base_currency": "EUR",
                    "quote_currency": currency,
                }
            )
    fresh_currencies = {
        currency for currency, latest in latest_by_currency.items()
        if (today - latest).days <= max_currency_lag_days
    }

    def complete_enough(currency: str) -> bool:
        years = sorted(years_by_currency.get(currency, set()))
        if len(years) < 2:
            return True
        expected = set(range(years[0], years[-1] + 1))
        missing = sorted(expected - set(years))
        if not missing:
            return True
        longest = run = 1
        for a, b in zip(missing, missing[1:]):
            run = run + 1 if b == a + 1 else 1
            longest = max(longest, run)
        return len(missing) / len(expected) <= 0.20 and longest < 3

    eligible_currencies = {c for c in fresh_currencies if complete_enough(c)}
    out = [r for r in out if r["quote_currency"] in eligible_currencies]
    out.sort(key=lambda r: (str(r["series_id"]), str(r["date"])))
    return out


def collect(*, log=print) -> list[dict[str, Any]]:
    raw = _fetch_zip()
    rows = normalize_csv(_csv_text(raw))
    if not rows:
        log("no ECB FX observations fetched; not writing an empty file")
        return []
    _write_jsonl_atomic(rows)
    currencies = sorted({r["quote_currency"] for r in rows})
    dates = sorted({r["date"] for r in rows})
    log(f"wrote {len(rows)} observations across {len(currencies)} currencies: {dates[0]} to {dates[-1]}")
    return rows


if __name__ == "__main__":
    print("ECB euro foreign-exchange reference rates (keyless official ZIP):")
    observations = collect()
    for row in observations[:5]:
        print("  " + json.dumps({k: row[k] for k in ("series_id", "date", "value", "unit", "title")}, ensure_ascii=False))
