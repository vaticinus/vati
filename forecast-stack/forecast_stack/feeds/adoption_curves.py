"""Adoption / diffusion curves — the REAL L5 demand layer (OWID).

Before this feed, pillar 5 ("Demand / adoption") looked full (127k series) but was almost
entirely mis-binned mining-permit records — i.e. demand was effectively blind. This feed lands
genuine adoption/diffusion S-curves: the rate at which a technology is actually being taken up.
Demand = the next layer's measurable supply-build, so a rising adoption share is the diffusion
signal that confirms a frontier capability is crossing into the real economy.

Keyless, redistributable OWID grapher CSVs only. A basket of major economies plus World, so the
diffusion can be read both globally and where it is leading. Each point dated to reference
year-end (never fetched_at); OWID publishes annual vintages after the reference year, so the
year-end stamp is conservative for forward use.
"""

from __future__ import annotations

import csv
import io
import json
import urllib.request
from datetime import date
from pathlib import Path
from forecast_stack.config import DATA_DIR
from typing import Any

UA = "forecast-stack (+https://github.com/vaticinus/vati)"
OUT_PATH = DATA_DIR / "feeds" / "adoption_curves.jsonl"
OWID = "https://ourworldindata.org/grapher/{slug}.csv"
WINDOW_START = 1980
CUTOFF_YEAR = 2025

ENTITIES = (
    "World", "United States", "China", "India", "Germany", "United Kingdom",
    "Japan", "European Union (27)", "France", "Italy", "Spain", "Netherlands",
    "Norway", "Sweden", "South Korea", "Canada", "Australia", "Brazil",
    "Indonesia", "Nigeria", "South Africa", "Mexico", "Russia", "Turkey",
    "Saudi Arabia", "Poland", "Vietnam", "Bangladesh", "Pakistan", "Kenya",
    "Ethiopia", "Denmark", "Switzerland", "Israel", "Singapore", "Chile",
)

# slug -> (csv_column, metric, unit, domain)
# Every slug verified to resolve (HTTP 200 on .csv) and column header checked.
CURVES: dict[str, tuple[str, str, str, str]] = {
    # --- transport: EV adoption ---
    "electric-car-sales-share": ("Share of new cars that are electric", "ev_sales_share",
                                 "%", "transport"),
    "electric-car-sales": ("Electric cars sold", "ev_units_sold", "count", "transport"),
    "electric-car-stocks": ("Electric car stocks", "ev_stock", "count", "transport"),
    # --- energy: generation mix shares (electricity) ---
    "share-electricity-renewables": ("Renewables", "renewable_electricity_share", "%", "energy"),
    "share-electricity-solar": ("Solar", "solar_electricity_share", "%", "energy"),
    "share-electricity-wind": ("Wind", "wind_electricity_share", "%", "energy"),
    "share-electricity-hydro": ("Hydropower", "hydro_electricity_share", "%", "energy"),
    "share-electricity-nuclear": ("Nuclear", "nuclear_electricity_share", "%", "energy"),
    "share-electricity-low-carbon": ("Share of electricity from low-carbon sources",
                                     "low_carbon_electricity_share", "%", "energy"),
    "share-electricity-fossil-fuels": ("Fossil fuels", "fossil_electricity_share", "%", "energy"),
    # --- energy: primary-energy mix shares ---
    "renewable-share-energy": ("Renewables", "renewable_primary_energy_share", "%", "energy"),
    "solar-share-energy": ("Solar", "solar_energy_share", "%", "energy"),
    "wind-share-energy": ("Wind", "wind_energy_share", "%", "energy"),
    "low-carbon-share-energy": ("Low-carbon energy", "low_carbon_energy_share", "%", "energy"),
    "fossil-fuel-primary-energy": ("Fossil fuels", "fossil_primary_energy_share", "%", "energy"),
    # --- energy: capacity / generation levels ---
    "installed-solar-pv-capacity": ("Solar", "installed_solar_pv_capacity", "GW", "energy"),
    "per-capita-electricity-generation": ("Per capita electricity use",
                                          "electricity_use_per_capita", "kWh", "energy"),
    # --- energy: access (electrification S-curve) ---
    "share-of-the-population-with-access-to-electricity":
        ("Share of the population with access to electricity", "electricity_access_share",
         "%", "energy"),
    # --- digital: connectivity adoption ---
    "share-of-individuals-using-the-internet": ("Share of the population using the Internet",
                                               "internet_user_share", "%", "digital"),
    "number-of-internet-users": ("Number of Internet users", "internet_users", "count", "digital"),
    "mobile-cellular-subscriptions-per-100-people":
        ("Mobile cellular subscriptions (per 100 people)", "mobile_subscriptions_per_100",
         "per 100", "digital"),
    "broadband-penetration-by-country":
        ("Fixed broadband subscriptions (per 100 people)", "fixed_broadband_per_100",
         "per 100", "digital"),
}


def _fetch(url: str, *, timeout: int = 60) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/csv"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 public CSV
        return resp.read().decode("utf-8-sig", "replace")


def _year(raw: Any) -> int | None:
    text = str(raw or "").strip()
    return int(text[:4]) if len(text) >= 4 and text[:4].isdigit() else None


def _num(raw: Any) -> float | None:
    text = str(raw or "").strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _slug_entity(entity: str) -> str:
    return entity.lower().replace(" ", "_").replace("(", "").replace(")", "")


def collect(*, log=print) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for slug, (col, metric, unit, domain) in CURVES.items():
        try:
            rows = list(csv.DictReader(io.StringIO(_fetch(OWID.format(slug=slug)))))
        except Exception as exc:  # noqa: BLE001
            log(f"  ! OWID {slug}: {exc}")
            continue
        n = 0
        for r in rows:
            entity = str(r.get("Entity") or "")
            if entity not in ENTITIES:
                continue
            year = _year(r.get("Year"))
            val = _num(r.get(col))
            if year is None or val is None or not (WINDOW_START <= year <= CUTOFF_YEAR):
                continue
            series_id = f"{metric}__{_slug_entity(entity)}"
            day = date(year, 12, 31).isoformat()
            out.append({
                "series_id": series_id, "date": day, "event_time": day,
                "observed_at": day, "published_at": day, "value": float(val),
                "unit": unit, "metric": metric, "domain": domain,
                "title": f"{col} ({entity})",
            })
            n += 1
        if n:
            log(f"  + {metric:<32s} {n:3d} obs  ({slug})")
    out.sort(key=lambda r: (str(r["series_id"]), str(r["date"])))
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for r in out:
            fh.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    tmp.replace(OUT_PATH)
    log(f"\nwrote {len(out)} adoption observations -> {OUT_PATH}")
    return out


if __name__ == "__main__":
    print("Adoption / diffusion curves (L5 demand) — keyless OWID:")
    collect()
