"""SEC EDGAR XBRL frames — capital-spend pillar collector (keyless, $0).

The CAPITAL-SPEND signal of the real economy, read straight off the source filings. SEC EDGAR's
XBRL "frames" API aggregates ONE us-gaap concept across ALL reporting companies for a fixed
calendar period — e.g. every company's PaymentsToAcquirePropertyPlantAndEquipment (capex) for
CY2023Q4. Summed across filers, that frame is an economy-wide capital-formation aggregate built
bottom-up from primary 10-K/10-Q filings, not a survey or an index.

KEYLESS: the only requirement EDGAR imposes is a descriptive `User-Agent` header (SEC fair-access
policy). No API key, no auth, no token. Endpoint shape:
  https://data.sec.gov/api/xbrl/frames/us-gaap/<CONCEPT>/USD/CY<YYYY>Q<Q>I.json   (instant / point-in-time)
  https://data.sec.gov/api/xbrl/frames/us-gaap/<CONCEPT>/USD/CY<YYYY>Q<Q>.json    (duration / flow)
Flow concepts (capex, revenue) are DURATION facts → use the non-"I" form per quarter; balance-sheet
levels (PP&E gross) are INSTANT facts → use the "I" form. Each fact in `.data[]` carries its own
`end` date (and `start` for durations) plus the filing `accn`/`form` — REAL, point-in-time dates
we keep verbatim. We never synthesize or backfill: a period with no published frame is simply absent.

NORMALIZATION: one frame = many company facts; we aggregate to a single economy-wide observation per
period (sum of USD across filers) and tag the count of filers behind it. `date` = the frame's period
`end` (the day the quarter closed — when the aggregate became knowable, modulo filing lag, see below).

LEAK-CLASS — CONFIRMATION/LAG. Aggregate corporate capex/revenue is a coincident-to-lagging read of
the priced economic outcome: (1) the fact's economic period has already ENDED on `end`, and (2) the
frame only fills in as companies FILE (10-Qs land ~40 days, 10-Ks ~60-90 days after period end), so a
just-closed quarter's frame is sparse and grows for months. The signal therefore moves WITH/BEHIND the
macro outcome the market has already priced — it CONFIRMS a capex cycle turning, it does not lead it.
(Honest placement: this would sit in LAG_PROVIDERS, like comtrade_china in forces.py.)

Run standalone: `uv run python forecast_stack/feeds/sec_edgar.py` → writes a sample to
data/feeds/sec_edgar.jsonl (one JSON object per line) and prints the first few.
"""

from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path
from forecast_stack.config import DATA_DIR

# SEC requires a descriptive UA with contact info (their fair-access policy) — NOT an API key.
UA = "forecast-stack (+https://github.com/vaticinus/vati)"
FRAMES = "https://data.sec.gov/api/xbrl/frames/us-gaap"

# Each concept: (us-gaap tag, human title, is_instant). Capital-spend pillar.
#   - PaymentsToAcquirePropertyPlantAndEquipment   = capex (cash-flow, DURATION/flow)
#   - PropertyPlantAndEquipmentGross                = installed PP&E level (balance-sheet, INSTANT)
#   - RevenueFromContractWithCustomerExcludingAssessedTax = topline (DURATION/flow)
CONCEPTS: list[tuple[str, str, bool]] = [
    ("PaymentsToAcquirePropertyPlantAndEquipment",
     "Aggregate corporate capex (payments to acquire PP&E)", False),
    ("PropertyPlantAndEquipmentGross",
     "Aggregate gross property, plant & equipment (installed capital)", True),
    ("RevenueFromContractWithCustomerExcludingAssessedTax",
     "Aggregate revenue from contracts with customers", False),
    # Capital-flow breadth (L6): innovation spend, M&A, and the two private/public funding channels.
    ("ResearchAndDevelopmentExpense",
     "Aggregate corporate R&D expense (innovation investment)", False),
    ("PaymentsToAcquireBusinessesNetOfCashAcquired",
     "Aggregate M&A spend (payments to acquire businesses, net of cash)", False),
    ("ProceedsFromIssuanceOfCommonStock",
     "Aggregate equity issuance (proceeds from common stock)", False),
    ("ProceedsFromIssuanceOfLongTermDebt",
     "Aggregate long-term debt issuance (proceeds)", False),
]
# Calendar quarters to pull (populated frames back to 2015). Point-in-time: each is knowable at its `end`.
YEARS = [2015, 2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024]

# Per-concept metric label so each capital channel is its own series-metric, not a generic frame.
_CONCEPT_METRIC = {
    "PaymentsToAcquirePropertyPlantAndEquipment": "corporate_capex",
    "PropertyPlantAndEquipmentGross": "installed_ppe",
    "RevenueFromContractWithCustomerExcludingAssessedTax": "corporate_revenue",
    "ResearchAndDevelopmentExpense": "corporate_rnd_expense",
    "PaymentsToAcquireBusinessesNetOfCashAcquired": "ma_spend",
    "ProceedsFromIssuanceOfCommonStock": "equity_issuance",
    "ProceedsFromIssuanceOfLongTermDebt": "debt_issuance",
}
QUARTERS = [1, 2, 3, 4]

OUT = DATA_DIR / "feeds" / "sec_edgar.jsonl"


def _fetch_json(url: str, *, retries: int = 2) -> dict | None:
    """One keyless GET of an EDGAR frame. None on persistent failure (e.g. a period with no frame
    returns HTTP 404 — that period simply has no published aggregate, we never fake it)."""
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip, deflate"})
            with urllib.request.urlopen(req, timeout=40) as resp:  # noqa: S310 keyless public endpoint
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    import gzip
                    raw = gzip.decompress(raw)
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            if e.code == 404:  # no frame for this period — legitimate absence, not an error to retry
                return None
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            return None
        except Exception:  # noqa: BLE001 — network/parse: back off, retry, then None
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            return None
    return None


def _frame_url(concept: str, year: int, q: int, instant: bool) -> str:
    suffix = "I" if instant else ""
    return f"{FRAMES}/{concept}/USD/CY{year}Q{q}{suffix}.json"


def _aggregate_frame(data: dict) -> tuple[float, int, str] | None:
    """Aggregate a frame's per-company USD facts → (total_usd, n_filers, period_end).

    A calendar frame mixes the dominant calendar-quarter filers with a long tail of off-calendar
    fiscal-period filers (e.g. a CY2021Q1 frame is ~89% `end`=2021-03-31 plus a few ending 04-30,
    04-03, …). We anchor on the MODAL `end` — the true calendar-quarter close, a real date carried
    by the facts — and sum only the facts reported AS OF that period, so the aggregate is a clean,
    comparable economy-wide total rather than blurring fiscal periods together. None if empty."""
    from collections import Counter
    facts = data.get("data") or []
    ends = Counter(f.get("end") for f in facts if f.get("end"))
    if not ends:
        return None
    period_end = ends.most_common(1)[0][0]  # modal end = the calendar-quarter close (a REAL date)
    total = 0.0
    n = 0
    for f in facts:
        if f.get("end") != period_end:
            continue
        try:
            total += float(f["val"])
        except (KeyError, ValueError, TypeError):
            continue
        n += 1
    if n == 0:
        return None
    return total, n, period_end


def collect(*, log=print) -> list[dict]:
    """Fetch the capital-spend frames and return normalized observations:
    {series_id, date:'YYYY-MM-DD', value:float, unit, title}. Keyless, $0. Real dates only."""
    obs: list[dict] = []
    for concept, title, instant in CONCEPTS:
        series_id = f"sec_edgar:us-gaap:{concept}"
        n_period = 0
        for year in YEARS:
            for q in QUARTERS:
                url = _frame_url(concept, year, q, instant)
                data = _fetch_json(url)
                time.sleep(0.25)  # be a good citizen (SEC asks for <10 req/s; we go far slower)
                if not data:
                    continue
                agg = _aggregate_frame(data)
                if not agg:
                    continue
                total, n_filers, end = agg
                obs.append({
                    "series_id": series_id,
                    "date": end,                       # REAL period-end date from the frame
                    "value": round(total, 2),
                    "unit": "USD",
                    "metric": _CONCEPT_METRIC.get(concept, "xbrl_frame"),
                    "domain": "capital",
                    "title": f"{title} — CY{year}Q{q} ({n_filers} filers)",
                })
                n_period += 1
        log(f"  + {concept:<52} {n_period} quarterly frames")
    return obs


if __name__ == "__main__":
    print("SEC EDGAR XBRL frames — capital-spend aggregates (keyless):")
    observations = collect()
    if not observations:
        print("\n! No observations fetched (EDGAR unreachable or no frames). Not writing fabricated data.")
        raise SystemExit(1)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w") as fh:
        for o in observations:
            fh.write(json.dumps(o) + "\n")
    print(f"\nWrote {len(observations)} observations to {OUT}")
    print("\nFirst few:")
    for o in observations[:5]:
        print(" ", json.dumps(o))
