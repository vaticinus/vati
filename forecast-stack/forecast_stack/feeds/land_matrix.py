"""Land Matrix global large-scale land-acquisition collector (keyless, $0).

Answers "to whom does what land belong, and is it taken or not" at world scale: the Land Matrix is the
reference public database of large-scale land deals (agriculture, forestry, mining, carbon). One
normalized row per deal: the target country (where the land is), the operating company + investors
(who holds/controls it), the size in hectares, what it is for, and the negotiation status that says
whether the land is actually TAKEN (contract signed) or merely intended / failed.

Why this matters for the engine: land is the ultimate inelastic input, and "already concessioned /
signed" is a direct supply-elasticity signal. If the prospective land in a jurisdiction is already
locked up, new supply cannot come even when price spikes. The investors/operators become named
real-world actors for the capture layer (who holds it -> who to talk to).

Keyless official API (landmatrix.org/api). Same JSONL contract as the other land feeds, so it flows
through the existing collect -> load -> entities/series path.
"""

from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from forecast_stack.config import DATA_DIR
from typing import Any

UA = "forecast-stack (+https://github.com/vaticinus/vati)"
API_BASE = "https://landmatrix.org/api"
OUT_PATH = DATA_DIR / "feeds" / "land_matrix.jsonl"

REQUEST_TIMEOUT_S = 60

# negotiation_status values that mean the land is (or was) actually held, vs merely intended/failed.
_TAKEN = {"CONTRACT_SIGNED", "CHANGE_OF_OWNERSHIP", "CONTRACT_EXPIRED", "ORAL_AGREEMENT"}
_NOT_TAKEN = {"NEGOTIATIONS_FAILED", "CONTRACT_CANCELED", "EXPRESSION_OF_INTEREST",
              "UNDER_NEGOTIATION", "MEMORANDUM_OF_UNDERSTANDING"}


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _fetch_json(url: str) -> Any:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json,*/*"})
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:  # noqa: S310 official public API
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 - public API; skip rather than fabricate
        return None


def _write_jsonl_atomic(rows: list[dict[str, Any]]) -> None:
    tmp = OUT_PATH.with_suffix(".jsonl.tmp")
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tmp.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    tmp.replace(OUT_PATH)


def _country_map() -> dict[int, dict[str, Any]]:
    data = _fetch_json(f"{API_BASE}/countries/")
    out: dict[int, dict[str, Any]] = {}
    if isinstance(data, list):
        for c in data:
            if isinstance(c, dict) and c.get("id") is not None:
                out[int(c["id"])] = c
    return out


def _year_end(value: Any) -> str | None:
    try:
        year = int(value)
    except (TypeError, ValueError):
        return None
    return f"{year:04d}-12-31" if 1800 <= year <= 2100 else None


def _clean_name(name: Any) -> str:
    """Land Matrix uses 'Unknown (...)' placeholders for un-named operators; drop those."""
    text = str(name or "").strip()
    if not text or text.lower().startswith("unknown"):
        return ""
    return text


def _holders(sv: dict[str, Any]) -> list[str]:
    names: list[str] = []
    oc = sv.get("operating_company")
    if isinstance(oc, dict):
        ocsv = oc.get("selected_version") if isinstance(oc.get("selected_version"), dict) else {}
        if not ocsv.get("name_unknown"):
            n = _clean_name(ocsv.get("name"))
            if n:
                names.append(n)
    for inv in sv.get("top_investors") or []:
        if isinstance(inv, dict):
            n = _clean_name(inv.get("name"))
            if n:
                names.append(n)
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _coords(sv: dict[str, Any]) -> list[float] | None:
    for loc in sv.get("locations") or []:
        if isinstance(loc, dict) and isinstance(loc.get("point"), dict):
            xy = loc["point"].get("coordinates")
            if isinstance(xy, list) and len(xy) == 2:
                return [round(float(xy[0]), 5), round(float(xy[1]), 5)]
    return None


def _row_from_deal(deal: dict[str, Any], countries: dict[int, dict[str, Any]]) -> dict[str, Any] | None:
    did = deal.get("id")
    sv = deal.get("selected_version") if isinstance(deal.get("selected_version"), dict) else {}
    if not did or not sv:
        return None
    cid = deal.get("country_id")
    country = countries.get(int(cid)) if cid is not None else None
    country_name = (country or {}).get("name") or ""
    status = sv.get("current_negotiation_status") or ""
    taken = 1 if status in _TAKEN else (0 if status in _NOT_TAKEN else None)
    size = sv.get("deal_size") or sv.get("current_contract_size") or sv.get("intended_size") or 0.0
    try:
        size = float(size)
    except (TypeError, ValueError):
        size = 0.0
    holders = _holders(sv)
    intention = [str(v) for v in (sv.get("current_intention_of_investment") or []) if v]
    crops = [str(v) for v in (sv.get("current_crops") or []) if v]
    init_year = sv.get("initiation_year")
    updated = str(deal.get("fully_updated_at") or "")[:10] or None
    event_time = _year_end(init_year) or updated or _today()
    holder_str = "; ".join(holders[:3]) or "investor undisclosed"
    title = f"Land Matrix deal {did}: {holder_str} -> {country_name or 'unknown country'}"
    if size:
        title += f" ({size:,.0f} ha)"
    if status:
        title += f" [{status}]"
    return {
        "feed": "land_matrix",
        "source_authority": "Land Matrix Global Observatory API",
        "source_page_url": f"https://landmatrix.org/deal/{did}/",
        "jurisdiction": country_name,
        "region": str((country or {}).get("region_id") or "Global"),
        "country_high_income": (country or {}).get("high_income"),
        "series_id": f"landmatrix:deal:{did}",
        "date": event_time,
        "as_of": event_time,
        "event_time": event_time,
        "published_at": updated or event_time,
        "observed_at": event_time,
        "value": size if size else 1.0,
        "unit": "hectares" if size else "deal",
        "metric": "land_acquisition_deal",
        "domain": "land_use",
        "title": title,
        "deal_id": int(did),
        "negotiation_status": status,
        "implementation_status": sv.get("current_implementation_status") or "",
        "land_taken": taken,
        "deal_size_ha": size,
        "intention_of_investment": intention,
        "current_crops": crops,
        "holders": holders,
        "operating_country": country_name,
        "initiation_year": init_year,
        "coordinates": _coords(sv),
        "cost_cents": 0,
        "provenance": "official_landmatrix_api",
    }


def collect(*, log=print, limit: int | None = None) -> list[dict[str, Any]]:
    countries = _country_map()
    log(f"  + countries map: {len(countries)} resolved")
    deals = _fetch_json(f"{API_BASE}/deals/")
    if not isinstance(deals, list):
        log("  - Land Matrix deals endpoint unreachable")
        return []
    log(f"  + deals endpoint: {len(deals)} deals")
    rows: list[dict[str, Any]] = []
    collected_at = datetime.now(timezone.utc).isoformat()
    for deal in deals:
        if not isinstance(deal, dict):
            continue
        row = _row_from_deal(deal, countries)
        if row:
            row["collected_at"] = collected_at
            rows.append(row)
        if limit and len(rows) >= limit:
            break
    rows.sort(key=lambda r: (str(r.get("event_time") or ""), int(r.get("deal_id") or 0)), reverse=True)
    if not rows:
        log(f"\nno Land Matrix rows fetched; preserved existing feed at {OUT_PATH}")
        return []
    _write_jsonl_atomic(rows)
    taken = sum(1 for r in rows if r.get("land_taken") == 1)
    named = sum(1 for r in rows if r.get("holders"))
    log(f"\nwrote {len(rows)} Land Matrix deals -> {OUT_PATH} ({taken} land-taken/signed, {named} with a named holder)")
    return rows


if __name__ == "__main__":
    print("Land Matrix global large-scale land deals (keyless official API):")
    observations = collect()
    if not observations:
        print("\nNO rows collected - Land Matrix API unreachable/empty this run.")
    else:
        print(f"\nfirst {min(5, len(observations))} rows:")
        for row in observations[:5]:
            print("  " + json.dumps({k: row[k] for k in (
                "deal_id", "jurisdiction", "negotiation_status", "land_taken", "deal_size_ha", "holders")},
                ensure_ascii=False))
