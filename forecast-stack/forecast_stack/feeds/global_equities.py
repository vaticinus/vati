"""Global equity/index close-price collector for the V1 world-state spine.

This is the small operational feed, not the historical S3 price lake. It pulls a bounded set of
major global indices and top-company tickers from Yahoo Finance's keyless chart endpoint, keeps only
daily closes since 2020 by default, and writes normalized JSONL for the existing feed ingest path.
"""

from __future__ import annotations

import json
import math
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from forecast_stack.config import DATA_DIR
from typing import Any

UA = "forecast-stack (+https://github.com/vaticinus/vati)"
YAHOO_CHART = "https://query2.finance.yahoo.com/v8/finance/chart/{symbol}"
OUT_PATH = DATA_DIR / "feeds" / "global_equities.jsonl"
REQUEST_TIMEOUT_S = float(os.environ.get("GLOBAL_EQUITIES_TIMEOUT_S", "20"))
REQUEST_SPACING_S = float(os.environ.get("GLOBAL_EQUITIES_SPACING_S", "0.25"))
DEFAULT_START = date.fromisoformat(os.environ.get("GLOBAL_EQUITIES_START", "2020-01-01"))
DEFAULT_LIMIT = int(os.environ.get("GLOBAL_EQUITIES_LIMIT", "60"))
MIN_REFRESH_FRACTION = float(os.environ.get("GLOBAL_EQUITIES_MIN_REFRESH_FRACTION", "0.5"))


@dataclass(frozen=True)
class EquitySpec:
    symbol: str
    market: str
    title: str
    kind: str = "equity"


UNIVERSE: tuple[EquitySpec, ...] = (
    EquitySpec("^GSPC", "US", "S&P 500", "index"),
    EquitySpec("^IXIC", "US", "NASDAQ Composite", "index"),
    EquitySpec("^SOX", "US", "PHLX Semiconductor Index", "index"),
    EquitySpec("^HSI", "HK", "Hang Seng Index", "index"),
    EquitySpec("^HSTECH", "HK", "Hang Seng Tech Index", "index"),
    EquitySpec("000300.SS", "CN", "CSI 300 Index", "index"),
    EquitySpec("^N225", "JP", "Nikkei 225", "index"),
    EquitySpec("^NSEI", "IN", "Nifty 50", "index"),
    EquitySpec("^KS11", "KR", "KOSPI", "index"),
    EquitySpec("^TWII", "TW", "Taiwan Weighted Index", "index"),
    EquitySpec("^FTSE", "UK", "FTSE 100", "index"),
    EquitySpec("^GDAXI", "DE", "DAX", "index"),
    EquitySpec("^FCHI", "FR", "CAC 40", "index"),
    EquitySpec("^STOXX50E", "EU", "EURO STOXX 50", "index"),
    EquitySpec("^GSPTSE", "CA", "S&P/TSX Composite", "index"),
    EquitySpec("^AXJO", "AU", "ASX 200", "index"),
    EquitySpec("^BVSP", "BR", "Bovespa", "index"),
    EquitySpec("NVDA", "US", "NVIDIA"),
    EquitySpec("TSM", "TW/US", "Taiwan Semiconductor Manufacturing Company ADR"),
    EquitySpec("ASML", "NL/US", "ASML ADR"),
    EquitySpec("005930.KS", "KR", "Samsung Electronics"),
    EquitySpec("000660.KS", "KR", "SK Hynix"),
    EquitySpec("INTC", "US", "Intel"),
    EquitySpec("AMD", "US", "Advanced Micro Devices"),
    EquitySpec("AVGO", "US", "Broadcom"),
    EquitySpec("QCOM", "US", "Qualcomm"),
    EquitySpec("AAPL", "US", "Apple"),
    EquitySpec("MSFT", "US", "Microsoft"),
    EquitySpec("GOOGL", "US", "Alphabet Class A"),
    EquitySpec("AMZN", "US", "Amazon"),
    EquitySpec("META", "US", "Meta Platforms"),
    EquitySpec("TSLA", "US", "Tesla"),
    EquitySpec("1211.HK", "HK", "BYD"),
    EquitySpec("300750.SZ", "CN", "CATL"),
    EquitySpec("TM", "JP/US", "Toyota ADR"),
    EquitySpec("PCRFY", "JP/US", "Panasonic ADR"),
    EquitySpec("051910.KS", "KR", "LG Chem"),
    EquitySpec("ENR.DE", "DE", "Siemens Energy"),
    EquitySpec("SU.PA", "FR", "Schneider Electric"),
    EquitySpec("ETN", "US", "Eaton"),
    EquitySpec("GEV", "US", "GE Vernova"),
    EquitySpec("ABBN.SW", "CH", "ABB"),
    EquitySpec("FSLR", "US", "First Solar"),
    EquitySpec("JKS", "CN/US", "JinkoSolar ADR"),
    EquitySpec("ALB", "US", "Albemarle"),
    EquitySpec("SQM", "CL/US", "SQM ADR"),
    EquitySpec("GLEN.L", "UK", "Glencore"),
    EquitySpec("BHP", "AU/US", "BHP ADR"),
    EquitySpec("RIO", "UK/US", "Rio Tinto ADR"),
    EquitySpec("VALE", "BR/US", "Vale ADR"),
    EquitySpec("FCX", "US", "Freeport-McMoRan"),
    EquitySpec("NVO", "DK/US", "Novo Nordisk ADR"),
    EquitySpec("LLY", "US", "Eli Lilly"),
    EquitySpec("NVS", "CH/US", "Novartis ADR"),
    EquitySpec("ROG.SW", "CH", "Roche"),
    EquitySpec("PFE", "US", "Pfizer"),
    EquitySpec("MRNA", "US", "Moderna"),
)


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _period_start(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())


def _period_end(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=timezone.utc).timestamp())


def _url(symbol: str, *, start: date, end: date) -> str:
    encoded = urllib.parse.quote(symbol, safe="")
    params = urllib.parse.urlencode(
        {
            "interval": "1d",
            "period1": _period_start(start),
            "period2": _period_end(end),
            "events": "history",
        }
    )
    return f"{YAHOO_CHART.format(symbol=encoded)}?{params}"


def _fetch_chart(symbol: str, *, start: date, end: date) -> bytes:
    req = urllib.request.Request(
        _url(symbol, start=start, end=end),
        headers={"User-Agent": UA, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:  # noqa: S310 keyless public endpoint
        return resp.read()


def _write_jsonl_atomic(rows: list[dict[str, Any]]) -> None:
    tmp = OUT_PATH.with_suffix(".jsonl.tmp")
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tmp.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    tmp.replace(OUT_PATH)


def _existing_line_count() -> int:
    if not OUT_PATH.exists():
        return 0
    with OUT_PATH.open(encoding="utf-8") as f:
        return sum(1 for _ in f)


def normalize_chart(
    spec: EquitySpec,
    raw: bytes | str | dict[str, Any],
    *,
    start: date = DEFAULT_START,
    today: date | None = None,
) -> list[dict[str, Any]]:
    today = today or _today()
    if isinstance(raw, bytes):
        data = json.loads(raw.decode("utf-8", "replace"))
    elif isinstance(raw, str):
        data = json.loads(raw)
    else:
        data = raw
    result = ((data.get("chart") or {}).get("result") or [None])[0]
    if not isinstance(result, dict):
        return []
    timestamps = result.get("timestamp") or []
    quote = (((result.get("indicators") or {}).get("quote") or [{}])[0]) or {}
    closes = quote.get("close") or []
    volumes = quote.get("volume") or []
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    currency = str(meta.get("currency") or "price")
    rows: list[dict[str, Any]] = []
    for idx, ts in enumerate(timestamps):
        try:
            d = datetime.fromtimestamp(int(ts), tz=timezone.utc).date()
            close = float(closes[idx])
        except (IndexError, TypeError, ValueError, OSError):
            continue
        if d < start or d > today or not math.isfinite(close):
            continue
        volume = None
        try:
            raw_volume = volumes[idx]
            volume = int(raw_volume) if raw_volume is not None else None
        except (IndexError, TypeError, ValueError):
            volume = None
        rows.append(
            {
                "series_id": f"global_equities:{spec.symbol}:close",
                "date": d.isoformat(),
                "value": round(close, 6),
                "unit": currency,
                "metric": "equity_close",
                "title": f"Global equities - {spec.title} close",
                "symbol": spec.symbol,
                "market": spec.market,
                "asset_kind": spec.kind,
                "volume": volume,
            }
        )
    return sorted(rows, key=lambda r: str(r["date"]))


def selected_universe(*, limit: int = DEFAULT_LIMIT) -> tuple[EquitySpec, ...]:
    if limit <= 0:
        return UNIVERSE
    return UNIVERSE[:limit]


def collect(*, log=print, start: date = DEFAULT_START, limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
    today = _today()
    rows: list[dict[str, Any]] = []
    ok = failed = 0
    for spec in selected_universe(limit=limit):
        try:
            raw = _fetch_chart(spec.symbol, start=start, end=today)
            parsed = normalize_chart(spec, raw, start=start, today=today)
        except Exception as exc:  # noqa: BLE001 - public market endpoints can throttle; preserve old file
            failed += 1
            log(f"  - {spec.symbol:<12s} fetch failed: {exc}")
            parsed = []
        if parsed:
            ok += 1
            rows.extend(parsed)
            log(f"  + {spec.symbol:<12s} {len(parsed):5d} obs  {parsed[0]['date']}-{parsed[-1]['date']}  {spec.title}")
        elif not failed:
            log(f"  - {spec.symbol:<12s} no parseable observations")
        time.sleep(REQUEST_SPACING_S)

    existing = _existing_line_count()
    rows.sort(key=lambda r: (str(r["series_id"]), str(r["date"])))
    if not rows:
        log(f"\nno global equity observations fetched; preserved existing {existing} rows at {OUT_PATH}")
        return []
    if existing and len(rows) < int(existing * MIN_REFRESH_FRACTION):
        log(
            f"\npartial refresh fetched {len(rows)} rows < "
            f"{MIN_REFRESH_FRACTION:.0%} of existing {existing}; preserved {OUT_PATH}"
        )
        return []
    _write_jsonl_atomic(rows)
    log(f"\nwrote {len(rows)} observations from {ok} symbols ({failed} failed) -> {OUT_PATH}")
    return rows


if __name__ == "__main__":
    print("Global equities/index daily closes (keyless Yahoo chart endpoint):")
    observations = collect()
    if not observations:
        print("\nNO observations collected - endpoint unreachable/empty this run.")
    else:
        print(f"\nfirst {min(5, len(observations))} observations:")
        for row in observations[:5]:
            print("  " + json.dumps({k: row[k] for k in ("series_id", "date", "value", "unit", "title")}, ensure_ascii=False))
        print(f"\njsonl line count: {_existing_line_count()}")
