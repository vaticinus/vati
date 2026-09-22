"""Supply elasticity — real capacity / utilization / backlog signals (FRED, keyless).

L4 ("where does scarcity rent land?") held trade flows and land-permit records but almost no
direct read of how TIGHT supply already is. Capacity utilization is the cleanest elasticity proxy:
a sector pinned near 100% cannot add output fast, so a demand shock lands as price/rent rather than
volume — exactly where the binding constraint pays. Industrial-production level, unfilled orders
(backlog), the inventory/sales ratio, and producer prices are the companion supply-tightness signals.

This is the WIDE version: many FRED capacity-utilization, industrial-production, unfilled-orders,
inventory/inventory-to-sales, and producer-price series across the constraint-bearing sectors —
semiconductors, computers/electronics, motor vehicles, aerospace, electric power, chemicals,
primary metals, machinery, fabricated metal, mining, and oil & gas. Each FRED id resolves to its own
series_id with a row-level metric/domain/unit/title so the changepoint detector sees per-sector
structural series, not one blended index.

Keyless FRED public CSV (the same endpoint the existing fred feed uses). Monthly observations dated
to their reference period; the Fed/Census/BLS release these ~2-6 weeks after period end, so a
just-closed month is a near-coincident read (documented in the trust rationale). Every id in SERIES
was verified to resolve (HTTP 200 + non-empty CSV) before being added.
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
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"
OUT_PATH = DATA_DIR / "feeds" / "supply_elasticity.jsonl"
WINDOW_START = 2000
CUTOFF_YEAR = 2026

# FRED id -> (metric, unit, domain, title). Every id verified to resolve (200 + non-empty) before
# adding. Grouped by signal family: capacity utilization (% of capacity), industrial production
# (index 2017=100), unfilled orders/backlog (USD million), inventories & inventory-to-sales, and
# producer prices (index). One series per FRED id; the row-level metric/domain feed straight through.
SERIES: dict[str, tuple[str, str, str, str]] = {
    # ---- capacity utilization (% of capacity) — the cleanest elasticity proxy ----
    "TCU": ("capacity_utilization_total", "%", "industry",
            "Total industry capacity utilization"),
    "MCUMFN": ("capacity_utilization_manufacturing", "%", "industry",
               "Manufacturing capacity utilization"),
    "CUMFNS": ("capacity_utilization_manufacturing_naics", "%", "industry",
               "Manufacturing (NAICS) capacity utilization"),
    "CAPUTLB50001SQ": ("capacity_utilization_total_index", "%", "industry",
                       "Total index capacity utilization (quarterly)"),
    "CAPUTLG211S": ("capacity_utilization_mining", "%", "minerals",
                    "Mining capacity utilization"),
    "CAPUTLG21S": ("capacity_utilization_mining_total", "%", "minerals",
                   "Mining (total) capacity utilization"),
    "CAPUTLG2122S": ("capacity_utilization_metal_ore_mining", "%", "minerals",
                     "Metal ore mining capacity utilization"),
    "CAPUTLG2123S": ("capacity_utilization_nonmetallic_mining", "%", "minerals",
                     "Nonmetallic mineral mining capacity utilization"),
    "CAPUTLG3311A2S": ("capacity_utilization_iron_steel", "%", "metals",
                       "Iron & steel capacity utilization"),
    "CAPUTLG331S": ("capacity_utilization_primary_metals", "%", "metals",
                    "Primary metals capacity utilization"),
    "CAPUTLG3344S": ("capacity_utilization_semiconductors", "%", "compute",
                     "Semiconductor & electronic components capacity utilization"),
    "CAPUTLG334S": ("capacity_utilization_computers_electronics", "%", "compute",
                    "Computer & electronic products capacity utilization"),
    "CAPUTLHITEK2S": ("capacity_utilization_high_tech", "%", "compute",
                      "High-tech industries capacity utilization"),
    "CAPUTLG335S": ("capacity_utilization_electrical_equipment", "%", "energy",
                    "Electrical equipment & appliances capacity utilization"),
    "CAPUTLG3361T3S": ("capacity_utilization_motor_vehicles", "%", "transport",
                       "Motor vehicles & parts capacity utilization"),
    "CAPUTLG336S": ("capacity_utilization_transportation_equipment", "%", "transport",
                    "Transportation equipment capacity utilization"),
    "CAPUTLG2211A2S": ("capacity_utilization_electric_power", "%", "energy",
                       "Electric power generation capacity utilization"),
    "CAPUTLG325S": ("capacity_utilization_chemicals", "%", "chemicals",
                    "Chemicals capacity utilization"),
    "CAPUTLG324S": ("capacity_utilization_petroleum_coal", "%", "energy",
                    "Petroleum & coal products capacity utilization"),
    "CAPUTLG326S": ("capacity_utilization_plastics_rubber", "%", "chemicals",
                    "Plastics & rubber products capacity utilization"),
    "CAPUTLG333S": ("capacity_utilization_machinery", "%", "industry",
                    "Machinery capacity utilization"),

    # ---- industrial production (index, 2017=100) — physical output level ----
    "INDPRO": ("industrial_production_total", "index 2017=100", "industry",
               "Total industrial production"),
    "IPGMFN": ("industrial_production_manufacturing", "index 2017=100", "industry",
               "Manufacturing industrial production"),
    "IPMINE": ("industrial_production_mining", "index 2017=100", "minerals",
               "Mining industrial production"),
    "IPUTIL": ("industrial_production_utilities", "index 2017=100", "energy",
               "Utilities industrial production"),
    "IPG2122S": ("industrial_production_metal_ore_mining", "index 2017=100", "minerals",
                 "Metal ore mining industrial production"),
    "IPG211S": ("industrial_production_oil_gas_extraction", "index 2017=100", "energy",
                "Oil & gas extraction industrial production"),
    "IPG331S": ("industrial_production_primary_metals", "index 2017=100", "metals",
                "Primary metals industrial production"),
    "IPG3311A2S": ("industrial_production_iron_steel", "index 2017=100", "metals",
                   "Iron & steel industrial production"),
    "IPG3344S": ("industrial_production_semiconductors", "index 2017=100", "compute",
                 "Semiconductor & electronic components industrial production"),
    "IPG334S": ("industrial_production_computers_electronics", "index 2017=100", "compute",
                "Computer & electronic products industrial production"),
    "IPG335S": ("industrial_production_electrical_equipment", "index 2017=100", "energy",
                "Electrical equipment & appliances industrial production"),
    "IPG3361T3S": ("industrial_production_motor_vehicles", "index 2017=100", "transport",
                   "Motor vehicles & parts industrial production"),
    "IPG3364T9S": ("industrial_production_aerospace_transport", "index 2017=100", "transport",
                   "Aerospace & other transportation equipment industrial production"),
    "IPG325S": ("industrial_production_chemicals", "index 2017=100", "chemicals",
                "Chemicals industrial production"),
    "IPG3254S": ("industrial_production_pharmaceuticals", "index 2017=100", "biotech",
                 "Pharmaceutical & medicine industrial production"),
    "IPG324S": ("industrial_production_petroleum_coal", "index 2017=100", "energy",
                "Petroleum & coal products industrial production"),
    "IPG326S": ("industrial_production_plastics_rubber", "index 2017=100", "chemicals",
                "Plastics & rubber products industrial production"),
    "IPG333S": ("industrial_production_machinery", "index 2017=100", "industry",
                "Machinery industrial production"),
    "IPDCONGD": ("industrial_production_durable_consumer_goods", "index 2017=100", "industry",
                 "Durable consumer goods industrial production"),

    # ---- unfilled orders / backlog (USD million) — lead-time / order-book tightness ----
    "AMTMUO": ("manufacturers_unfilled_orders", "USD million", "industry",
               "Manufacturers' unfilled orders (total backlog)"),
    "AMDMUO": ("durable_goods_unfilled_orders", "USD million", "industry",
               "Durable goods unfilled orders (backlog)"),
    "A31SUO": ("primary_metals_unfilled_orders", "USD million", "metals",
               "Primary metals unfilled orders (backlog)"),
    "A34SUO": ("machinery_unfilled_orders", "USD million", "industry",
               "Machinery unfilled orders (backlog)"),
    "ANXAUO": ("nondefense_aircraft_unfilled_orders", "USD million", "transport",
               "Nondefense aircraft & parts unfilled orders (backlog)"),
    "A36SNO": ("computers_electronics_new_orders", "USD million", "compute",
               "Computers & electronic products new orders"),
    "AODGUO": ("capital_goods_unfilled_orders", "USD million", "industry",
               "Capital goods unfilled orders (backlog)"),
    "AMTMNO": ("manufacturers_new_orders", "USD million", "industry",
               "Manufacturers' new orders (total)"),
    "NEWORDER": ("core_capital_goods_new_orders", "USD million", "industry",
                 "Nondefense capital goods ex-aircraft new orders"),

    # ---- inventories & inventory-to-sales — buffer depletion / restock pressure ----
    "AMTMTI": ("manufacturers_total_inventories", "USD million", "industry",
               "Manufacturers' total inventories"),
    "AMTMIS": ("manufacturers_inventories_to_sales", "ratio", "industry",
               "Manufacturers' inventories-to-sales ratio"),
    "BUSINV": ("total_business_inventories", "USD million", "industry",
               "Total business inventories"),
    "ISRATIO": ("total_business_inventories_to_sales", "ratio", "industry",
                "Total business inventories-to-sales ratio"),
    "TOTBUSSMNSA": ("total_business_sales", "USD million", "industry",
                    "Total business sales (NSA)"),
    "TOTBUSIMNSA": ("total_business_inventories_nsa", "USD million", "industry",
                    "Total business inventories (NSA)"),
    "MNFCTRIRSA": ("manufacturers_inventories_to_sales_sa", "ratio", "industry",
                   "Manufacturers' inventories-to-sales ratio (SA)"),
    "RETAILIRSA": ("retailers_inventories_to_sales", "ratio", "industry",
                   "Retailers' inventories-to-sales ratio"),
    "WHLSLRIRSA": ("wholesalers_inventories_to_sales", "ratio", "industry",
                   "Wholesalers' inventories-to-sales ratio"),

    # ---- producer prices (index) — where binding-constraint scarcity reprices first ----
    "PPIIDC": ("ppi_industrial_commodities", "index", "industry",
               "PPI: industrial commodities"),
    "WPU10": ("ppi_metals_metal_products", "index", "metals",
              "PPI: metals & metal products"),
    "WPU101": ("ppi_iron_steel", "index", "metals",
               "PPI: iron & steel"),
    "WPU1017": ("ppi_steel_mill_products", "index", "metals",
                "PPI: steel mill products"),
    "PCU331110331110": ("ppi_iron_steel_mills", "index", "metals",
                        "PPI: iron & steel mills & ferroalloy"),
    "PCU33113311": ("ppi_primary_metal_mfg", "index", "metals",
                    "PPI: primary metal manufacturing"),
    "WPU0613": ("ppi_industrial_chemicals", "index", "chemicals",
                "PPI: industrial chemicals"),
    "PCU3344133441": ("ppi_semiconductors", "index", "compute",
                      "PPI: semiconductor & related device mfg"),
    "PCU334413334413": ("ppi_semiconductor_devices", "index", "compute",
                        "PPI: semiconductor & related devices"),
    "PCU33443344": ("ppi_computers_electronics", "index", "compute",
                    "PPI: computer & electronic product mfg"),
    "WPU117": ("ppi_electrical_machinery_equipment", "index", "energy",
               "PPI: electrical machinery & equipment"),
    "WPU057": ("ppi_chemicals_allied_products", "index", "chemicals",
               "PPI: chemicals & allied products"),
    "WPU061": ("ppi_industrial_chemicals_broad", "index", "chemicals",
               "PPI: industrial chemicals (broad)"),
    "WPU065": ("ppi_plastic_resins_materials", "index", "chemicals",
               "PPI: plastic resins & materials"),
    "WPS061": ("ppi_industrial_chemicals_alt", "index", "chemicals",
               "PPI: industrial chemicals (alt series)"),
    "WPU0911": ("ppi_pulp_paper_products", "index", "industry",
                "PPI: pulp, paper & allied products"),
    "WPSFD4131": ("ppi_finished_goods_energy", "index", "energy",
                  "PPI: finished goods (energy-related)"),
    "PCU212112212112": ("ppi_bituminous_coal_mining", "index", "energy",
                        "PPI: bituminous coal underground mining"),
}


def _fetch_csv(series_id: str, *, timeout: int = 45) -> str | None:
    url = f"{FRED_CSV}?id={series_id}"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/csv"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 public CSV
            text = resp.read().decode("utf-8-sig", "replace")
    except Exception:  # noqa: BLE001
        return None
    if "<html" in text[:200].lower():
        return None
    return text


def _num(raw: Any) -> float | None:
    text = str(raw or "").strip()
    if not text or text == ".":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def collect(*, log=print) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for fred_id, (metric, unit, domain, title) in SERIES.items():
        text = _fetch_csv(fred_id)
        if text is None:
            log(f"  ! {fred_id}: no data")
            continue
        rows = list(csv.reader(io.StringIO(text)))
        n = 0
        for r in rows[1:]:  # skip header (observation_date,<id>)
            if len(r) < 2:
                continue
            d = str(r[0]).strip()[:10]
            try:
                y = int(d[:4])
            except ValueError:
                continue
            if not (WINDOW_START <= y <= CUTOFF_YEAR):
                continue
            val = _num(r[1])
            if val is None:
                continue
            out.append({
                "series_id": metric, "date": d, "event_time": d,
                "observed_at": d, "published_at": d, "value": float(val),
                "unit": unit, "metric": metric, "domain": domain, "title": title,
            })
            n += 1
        if n:
            log(f"  + {metric:<42s} {n:4d} obs  ({fred_id})")
    out.sort(key=lambda r: (str(r["series_id"]), str(r["date"])))
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for r in out:
            fh.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    tmp.replace(OUT_PATH)
    log(f"\nwrote {len(out)} supply-elasticity observations -> {OUT_PATH}")
    return out


if __name__ == "__main__":
    print("Supply elasticity (L4) — keyless FRED capacity/backlog:")
    collect()
