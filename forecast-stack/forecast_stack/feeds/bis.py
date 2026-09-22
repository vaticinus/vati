"""BIS (Bank for International Settlements) global financial statistics — keyless collector.

The BIS Data Portal exposes its full warehouse through a public, KEYLESS SDMX-REST v2 API at
https://stats.bis.org/api/v2/data/dataflow/BIS/<flow>/<version>/<key>?format=jsondata . A plain HTTPS
GET with `Accept: application/vnd.sdmx.data+json` returns SDMX-JSON (the same envelope oecd.py parses).
No API key, no auth, $0.

Why BIS — and why it is the de-US-biasing lever for the capital + pricing layers:
  The substrate's capital/pricing layers were dominated by US sources (FRED, SEC, usaspending). BIS is
  the one official, GLOBALLY-comparable, cross-country financial statistics provider — every series is
  published on the SAME methodology across the G20, so adding it widens geography without mixing
  apples and oranges. We pin three verified, high-signal dataflows:

    * WS_CBPOL  — central-bank POLICY RATES, monthly, per economy. The policy rate is the price of
      money; turns lead credit and the cycle. Routes to PRICING (the priced cost of capital), leak=leading.
    * WS_EER    — nominal broad EFFECTIVE EXCHANGE RATES, monthly, per economy. A currency's
      trade-weighted price; moves ahead of trade/inflation pass-through. Routes to PRICING, leak=leading.
    * WS_CREDIT_GAP — the BIS CREDIT-TO-GDP GAP, quarterly, per economy. The BIS's own early-warning
      indicator for financial crises (credit running ahead of trend). Routes to CAPITAL (the build-up of
      private leverage), leak=leading. Included opportunistically — economies returning no data are
      skipped, never filled.

Every observation carries its REAL reporting period parsed from the SDMX TIME_PERIOD dimension. Nothing
is synthesized, backfilled, or smoothed: a period absent from the response is absent here. A dataflow/key
that 404s ("NoResultsFound") or 422s (bad key arity) degrades honestly to "no observations" for that
economy — never a fabricated point.

Self-contained: does NOT touch the DB/cli/schemas. Run directly:  uv run python -m engine.feeds.bis
"""

from __future__ import annotations

import json
import time
import urllib.request
from datetime import date
from pathlib import Path
from forecast_stack.config import DATA_DIR

UA = "forecast-stack (+https://github.com/vaticinus/vati)"
OUT_PATH = DATA_DIR / "feeds" / "bis.jsonl"
BASE = "https://stats.bis.org/api/v2/data/dataflow/BIS"
ACCEPT = "application/vnd.sdmx.data+json"
START_PERIOD = "2015"

# BIS uses 2-letter ISO country codes (plus a few aggregates) in REF_AREA. The G20 + a few extra
# economies — deliberately spanning advanced + emerging markets to de-US-bias the layer.
ECONOMIES: tuple[tuple[str, str], ...] = (
    ("US", "United States"), ("XM", "Euro area"), ("GB", "United Kingdom"), ("JP", "Japan"),
    ("CN", "China"), ("DE", "Germany"), ("FR", "France"), ("IT", "Italy"), ("CA", "Canada"),
    ("AU", "Australia"), ("KR", "South Korea"), ("IN", "India"), ("BR", "Brazil"),
    ("RU", "Russia"), ("MX", "Mexico"), ("ID", "Indonesia"), ("TR", "Turkey"),
    ("SA", "Saudi Arabia"), ("ZA", "South Africa"), ("CH", "Switzerland"), ("SE", "Sweden"),
    ("HK", "Hong Kong SAR"), ("SG", "Singapore"),
)

# (slug, dataflow, version, key_template, metric, unit, pillar, leak, title_fmt)
#   key_template uses {c} for the 2-letter economy code.
DATAFLOWS: tuple[dict, ...] = (
    dict(slug="policy_rate", flow="WS_CBPOL", ver="1.0", key="M.{c}",
         metric="bis_policy_rate", unit="% p.a.", pillar=7, leak="leading",
         title="BIS central-bank policy rate"),
    dict(slug="eff_exch_rate", flow="WS_EER", ver="1.0", key="M.N.B.{c}",
         metric="bis_nominal_eer", unit="index", pillar=7, leak="leading",
         title="BIS nominal broad effective exchange rate"),
    dict(slug="credit_gap", flow="WS_CREDIT_GAP", ver="1.0", key="Q.{c}.P.A",
         metric="bis_credit_to_gdp_gap", unit="% of GDP", pillar=6, leak="leading",
         title="BIS credit-to-GDP gap (early-warning)"),
)


def _fetch_json(url: str, *, retries: int = 2):
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": ACCEPT})
            with urllib.request.urlopen(req, timeout=45) as resp:  # noqa: S310 keyless public endpoint
                return json.loads(resp.read().decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001 — 404/422/network: back off, retry, then None (never fake)
            if attempt < retries:
                time.sleep(1.0 * (attempt + 1))
                continue
            return None


def _period_to_date(period: str) -> date | None:
    """SDMX TIME_PERIOD → the real date it covers. Monthly 'YYYY-MM' → first-of-month; quarterly
    'YYYY-Qn' → last day of quarter; annual 'YYYY' → year-end. Unparseable → dropped, never guessed."""
    p = period.strip()
    if len(p) == 7 and p[4] == "-" and p[5] != "Q":
        try:
            return date(int(p[:4]), int(p[5:7]), 1)
        except ValueError:
            return None
    if "Q" in p and len(p) == 7:
        try:
            y, q = int(p[:4]), int(p[6])
            return date(y, 3 * q, 1)  # anchor to first-of-last-month-of-quarter
        except (ValueError, IndexError):
            return None
    if len(p) == 4 and p.isdigit():
        return date(int(p), 12, 31)
    return None


def _normalize(payload: dict, *, flow_meta: dict, code: str, name: str) -> list[dict]:
    try:
        data = payload["data"]
        # BIS SDMX-JSON uses singular `structure`; OECD uses plural `structures`. Accept both.
        struct = data["structures"][0] if "structures" in data else data["structure"]
        ds = data["dataSets"][0]
    except (KeyError, IndexError, TypeError):
        return []
    obs_dims = struct.get("dimensions", {}).get("observation", [])
    time_dim = next((d for d in obs_dims if d.get("id") == "TIME_PERIOD"), None)
    if not time_dim:
        return []
    time_values = [v.get("id", "") for v in time_dim.get("values", [])]
    series_id = f"bis:{flow_meta['slug']}:{code}"
    title = f"{flow_meta['title']} — {name}"
    out: list[dict] = []
    for _key, sobj in (ds.get("series") or {}).items():
        for obs_idx, obs_arr in (sobj.get("observations") or {}).items():
            try:
                period = time_values[int(obs_idx)]
            except (ValueError, IndexError):
                continue
            d = _period_to_date(period)
            if d is None or not obs_arr or obs_arr[0] is None:
                continue
            try:
                value = float(obs_arr[0])
            except (TypeError, ValueError):
                continue
            out.append({
                "series_id": series_id, "date": d.isoformat(), "value": value,
                "unit": flow_meta["unit"], "metric": flow_meta["metric"],
                "domain": "capital" if flow_meta["pillar"] == 6 else "pricing",
                "title": title,
            })
    out.sort(key=lambda o: o["date"])
    return out


def collect(*, log=print) -> list[dict]:
    all_obs: list[dict] = []
    for fm in DATAFLOWS:
        landed = 0
        for code, name in ECONOMIES:
            key = fm["key"].format(c=code)
            url = f"{BASE}/{fm['flow']}/{fm['ver']}/{key}?startPeriod={START_PERIOD}&format=jsondata"
            payload = _fetch_json(url)
            time.sleep(0.25)
            if payload is None:
                continue
            obs = _normalize(payload, flow_meta=fm, code=code, name=name)
            if obs:
                all_obs.extend(obs)
                landed += 1
        log(f"  + {fm['slug']:<16s} {landed}/{len(ECONOMIES)} economies")
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for o in all_obs:
            f.write(json.dumps(o, ensure_ascii=False, sort_keys=True) + "\n")
    tmp.replace(OUT_PATH)
    log(f"\nwrote {len(all_obs)} observations across "
        f"{len({o['series_id'] for o in all_obs})} series → {OUT_PATH}")
    return all_obs


if __name__ == "__main__":
    print("BIS global financial statistics (keyless SDMX-REST, stats.bis.org):")
    observations = collect()
    if not observations:
        print("\nNO observations collected — BIS SDMX unreachable this run (no data written).")
    else:
        print(f"\n{len(observations)} obs across {len({o['series_id'] for o in observations})} series.")
        for o in observations[:3]:
            print("  " + json.dumps({k: o[k] for k in ('series_id', 'date', 'value', 'unit')}, ensure_ascii=False))
