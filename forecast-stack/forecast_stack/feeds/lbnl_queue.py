"""LBNL "Queued Up" — the FULL interconnection-queue dataset (keyless, the GRID-QUEUE pillar).

The grid interconnection queue is the binding physical constraint on US energy / AI-datacenter
buildout: a gigawatt that clears the queue is a gigawatt that can actually be built. Lawrence
Berkeley National Lab (Berkeley Lab Electricity Markets & Policy, emp.lbl.gov) compiles, cleans and
publishes the underlying project-level queue DATA — not just the "Queued Up" report PDF — as an open,
KEYLESS Excel workbook each year. Berkeley Lab aggregates the queues of all seven ISO/RTOs (CAISO,
ERCOT, ISO-NE, MISO, NYISO, PJM, SPP) plus ~50 non-ISO utilities, ~98% of installed US generating
capacity, into one harmonized dataset with ~35 pre-summarized metric tabs.

This feed SUPERSEDES the 3-row hand-typed `forecast_stack/feeds/lbnl.py` (which carried only three headline
GW totals). It downloads the workbook and turns its summary tabs into proper TIME SERIES:

  • Annual requests              (05) — count + GW entering queues, per request year.
  • Active capacity by cohort    (06) — active GW by the year a request entered the queue (entry recency).
  • Active capacity by type      (07) — active GW by resource type × standalone/hybrid × snapshot year.
  • Active capacity region×type  (08) — active GW by region × resource type × snapshot year.
  • Queues vs. installed         (09) — queued GW vs installed GW by type (2010 & 2024 snapshots).
  • IA throughput by region      (18) — GW + count of interconnection agreements executed, per year × region.
  • Operational volume trend     (19) — GW coming online per year.
  • Withdrawn volume trend       (20) — count + GW withdrawn per year (the withdrawal channel).
  • Completion-rate trend        (21) — count + MW by final status (active/operational/withdrawn/
                                        suspended) per request-cohort year — the "do projects complete?" data.
  • Completion rate × type       (22) — MW + count by status × resource type (vintage snapshot).
  • Completion rate × region     (23) — MW + count by status × region (vintage snapshot).
  • Time-in-queue IR→COD         (35) — median (+p25/p75) months from request to commercial operation,
                                        by in-service year — the typical-time-in-queue series.

LEAK DISCIPLINE. Every observation carries its REAL data year, never today:
  • Cohort / snapshot / request / withdrawn / in-service year → `date` = Dec-31 of THAT year.
  • Cross-sectional snapshot tabs (status-by-type/region, queues-vs-installed) carry the dataset's
    data-through year (the point at which that cross-section is true), recorded explicitly.
  • `published_at` is the workbook's actual release date (2025-08 for the through-2024 vintage), NOT
    fetched_at. Nothing is synthesized, interpolated, or back/forward-filled. Non-numeric cells
    ('NA', blank, '-') are DROPPED, never coerced.

LEAK-CLASS — LAG / CONFIRMATION + LEADING-EDGE. The queue is forward-looking by nature (it is the
pipeline of plants not yet built), so the active-capacity and request series are a LEADING indicator
of where supply *wants* to build. The completion-rate / withdrawal / time-in-queue series are a
LAGGING, authoritative record of how much of that pipeline actually clears — exactly the realism
discount the headline GW number hides. Together they ground the supply-elasticity layer for grid.

normalized observation shape (one JSON object per jsonl line):
  {series_id, date:'YYYY-MM-DD', value:float, unit, metric, domain:'energy/grid', title,
   source_url, published_at, ...context}

$0, keyless. Run directly:  uv run python forecast_stack/feeds/lbnl_queue.py
"""

from __future__ import annotations

import io
import json
import re
import time
import urllib.request
import zipfile
from pathlib import Path
from forecast_stack.config import DATA_DIR
from typing import Any, Callable
from xml.etree import ElementTree as ET

UA = "forecast-stack (+https://github.com/vaticinus/vati)"
OUT_PATH = DATA_DIR / "feeds" / "lbnl_queue.jsonl"
DOMAIN = "energy/grid"
REQUEST_TIMEOUT_S = 60
FETCH_RETRIES = 2

# The LBNL "Queued Up" interconnection-queue DATA FILE (keyless public download, ~14 MB .xlsx).
# Resolved from the emp.lbl.gov "US Interconnection Queue Data" publication page; the file is served
# from eta-publications.lbl.gov (the same Drupal file store the report PDFs use). This vintage's
# project-level data runs through end-2024; the workbook was released 2025-08.
DATA_FILE_URL = (
    "https://eta-publications.lbl.gov/sites/default/files/2025-08/"
    "lbnl_ix_queue_data_file_thru2024_v2.xlsx"
)
PUBLISHED_AT = "2025-08-31"            # workbook release month (2025-08); leak: not fetched_at
DATA_THROUGH_YEAR = 2024              # project-level data runs through end-2024 (this vintage)
SOURCE_URL = "https://emp.lbl.gov/queues"

# Non-numeric cells in the workbook that mean "no genuine number" → DROP, never coerce.
_NULL = {"", "na", "n/a", "nd", "-", "--", "(s)", "xx", "none"}


# ── network ──────────────────────────────────────────────────────────────────
def _fetch_bytes(url: str, *, timeout: int = REQUEST_TIMEOUT_S, retries: int = FETCH_RETRIES) -> bytes | None:
    """Keyless public GET → raw bytes. Returns None on persistent failure (never fabricates)."""
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 keyless public endpoint
                return resp.read()
        except Exception:  # noqa: BLE001 — network/throttle: back off, retry, then None
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            return None


# ── minimal stdlib .xlsx reader (no openpyxl dependency) ─────────────────────
# An .xlsx is a zip of XML. We only need: sheet-name → file, the shared-string table, and each
# target sheet's cell values laid out into a row grid. This keeps the feed stdlib-only (CLAUDE.md:
# justify every dep against deleting it) and is plenty for these small pre-summarized tabs.
_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PKG_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_CELL_RE = re.compile(r"^([A-Z]+)(\d+)$")


def _col_to_idx(ref: str) -> int:
    """Excel cell ref ('B7') → 0-based column index."""
    m = _CELL_RE.match(ref)
    letters = m.group(1) if m else ref
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n - 1


def _shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    out: list[str] = []
    for si in root.findall(f"{_NS}si"):
        # a shared string is the concatenation of all its <t> runs
        out.append("".join(t.text or "" for t in si.iter(f"{_NS}t")))
    return out


def _sheet_targets(zf: zipfile.ZipFile) -> dict[str, str]:
    """Map worksheet NAME → archive path (xl/worksheets/sheetN.xml)."""
    rels_root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    rid_to_target = {
        r.get("Id"): r.get("Target") for r in rels_root.findall(f"{_PKG_REL_NS}Relationship")
    }
    wb_root = ET.fromstring(zf.read("xl/workbook.xml"))
    out: dict[str, str] = {}
    for sheet in wb_root.find(f"{_NS}sheets").findall(f"{_NS}sheet"):
        name = sheet.get("name")
        rid = sheet.get(f"{_REL_NS}id")
        target = rid_to_target.get(rid)
        if not name or not target:
            continue
        out[name] = target if target.startswith("xl/") else "xl/" + target.lstrip("/")
    return out


def _read_sheet_rows(zf: zipfile.ZipFile, path: str, sst: list[str]) -> list[tuple]:
    """Parse one worksheet XML into a list of row tuples (cells indexed by column, None for blanks)."""
    root = ET.fromstring(zf.read(path))
    sheet_data = root.find(f"{_NS}sheetData")
    if sheet_data is None:
        return []
    rows: list[tuple] = []
    for row_el in sheet_data.findall(f"{_NS}row"):
        cells: dict[int, Any] = {}
        max_c = -1
        for c in row_el.findall(f"{_NS}c"):
            ref = c.get("r") or ""
            ci = _col_to_idx(ref) if ref else len(cells)
            ctype = c.get("t")
            v_el = c.find(f"{_NS}v")
            val: Any = None
            if ctype == "s":  # shared string index
                if v_el is not None and v_el.text is not None:
                    idx = int(v_el.text)
                    val = sst[idx] if 0 <= idx < len(sst) else None
            elif ctype == "inlineStr":
                is_el = c.find(f"{_NS}is")
                if is_el is not None:
                    val = "".join(t.text or "" for t in is_el.iter(f"{_NS}t"))
            elif v_el is not None and v_el.text is not None:
                txt = v_el.text
                try:
                    val = float(txt)
                except ValueError:
                    val = txt
            cells[ci] = val
            if ci > max_c:
                max_c = ci
        rows.append(tuple(cells.get(i) for i in range(max_c + 1)))
    return rows


# ── value / label helpers ────────────────────────────────────────────────────
def _num(v: Any) -> float | None:
    """A cell → float, or None if it is blank / a non-numeric flag (DROP, never coerce)."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "")
    if s.lower() in _NULL:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _year(v: Any) -> int | None:
    """A cell → 4-digit calendar year, or None."""
    n = _num(v)
    if n is None:
        return None
    y = int(round(n))
    return y if 1990 <= y <= 2035 else None


def _slug(s: str) -> str:
    out = []
    for ch in str(s).strip().lower():
        if ch.isalnum():
            out.append(ch)
        elif ch in " -/+&":
            out.append("_")
        # drop *, (), etc. (the workbook uses ** / * footnote marks on type labels)
    slug = "".join(out)
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug.strip("_")


def _clean_label(s: str) -> str:
    """Human label: strip footnote asterisks the workbook appends to type names (Wind**, Other***)."""
    return str(s).replace("*", "").strip()


def _dec31(year: int) -> str:
    return f"{year:04d}-12-31"


def _find_header(rows: list[tuple], must_have: set[str], scan_to: int = 30) -> int | None:
    """Return the index of the first row (within scan_to) whose lowercased cells contain every
    token in must_have. Used to locate each tab's tidy data table beneath its chart/title block."""
    want = {t.lower() for t in must_have}
    for i, row in enumerate(rows[:scan_to]):
        cells = {str(c).strip().lower() for c in row if c is not None}
        if want.issubset(cells):
            return i
    return None


def _col_index(header_row: tuple, name: str) -> int | None:
    target = name.strip().lower()
    for i, c in enumerate(header_row):
        if c is not None and str(c).strip().lower() == target:
            return i
    return None


# ── per-tab parsers ──────────────────────────────────────────────────────────
# Each returns a list of observations. They are defensive: a tab whose layout has shifted between
# vintages simply yields [] (logged), it never fabricates and never sinks the run.

def _emit(series_id: str, year: int, value: float, *, unit: str, metric: str, title: str,
          data_year: int | None = None, **ctx: Any) -> dict:
    obs = {
        "series_id": series_id,
        "date": _dec31(year),
        "value": float(value),
        "unit": unit,
        "metric": metric,
        "domain": DOMAIN,
        "title": title,
        "source_url": SOURCE_URL,
        "published_at": PUBLISHED_AT,
    }
    if data_year is not None:
        obs["data_through_year"] = data_year
    obs.update(ctx)
    return obs


def parse_annual_requests(rows: list[tuple]) -> list[dict]:
    """05 — count + GW of requests entering queues, per request year."""
    h = _find_header(rows, {"Request Year", "n", "Capacity (GW)"})
    if h is None:
        return []
    out: list[dict] = []
    for row in rows[h + 1:]:
        y = _year(row[0])
        if y is None:
            continue
        n, gw = _num(row[1]), _num(row[2])
        if n is not None:
            out.append(_emit("lbnl_queue:annual_requests:count", y, n, unit="projects",
                             metric="queue_requests_count",
                             title="Interconnection requests entering queues (count)"))
        if gw is not None:
            out.append(_emit("lbnl_queue:annual_requests:gw", y, gw, unit="GW",
                             metric="queue_requests_capacity",
                             title="Interconnection requests entering queues (capacity)"))
    return out


def parse_active_by_cohort(rows: list[tuple]) -> list[dict]:
    """06 — cumulative active queue GW by the year a request entered the queue. The `Category` column
    splits capacity that entered in the year shown vs in an earlier year (the workbook's stacked-bar
    decomposition of the active queue by entry-cohort recency)."""
    h = _find_header(rows, {"Year", "Category", "Capacity (GW)"})
    if h is None:
        return []
    out: list[dict] = []
    for row in rows[h + 1:]:
        y = _year(row[0])
        gw = _num(row[2])
        if y is None or gw is None:
            continue
        cat = str(row[1] or "").strip().lower()
        cohort = "earlier_year" if "earlier" in cat else "year_shown"
        out.append(_emit(f"lbnl_queue:active_by_cohort:{cohort}", y, gw, unit="GW",
                         metric="queue_active_capacity",
                         title=f"Active queue capacity by entry cohort (entered in {cohort.replace('_', ' ')})",
                         cohort=cohort))
    return out


def parse_active_by_type(rows: list[tuple]) -> list[dict]:
    """07 — active queue GW by resource type × configuration (standalone/hybrid) × snapshot year."""
    h = _find_header(rows, {"Type", "Year", "Configuration", "Capacity (GW)"})
    if h is None:
        return []
    out: list[dict] = []
    for row in rows[h + 1:]:
        typ, y, cfg, gw = row[0], _year(row[1]), row[2], _num(row[3])
        if not typ or y is None or gw is None:
            continue
        typ_l = _clean_label(typ)
        cfg_l = str(cfg or "").strip().lower() or "all"
        out.append(_emit(f"lbnl_queue:active_by_type:{_slug(typ_l)}:{_slug(cfg_l)}", y, gw,
                         unit="GW", metric="queue_active_capacity",
                         title=f"Active queue capacity — {typ_l} ({cfg_l})",
                         resource_type=typ_l, configuration=cfg_l))
    return out


def parse_active_region_type(rows: list[tuple]) -> list[dict]:
    """08 — active queue GW by region × resource type × snapshot year (regions are ISO/RTOs + West/
    Southeast)."""
    h = _find_header(rows, {"Region", "Year", "type", "Capacity (GW)"})
    if h is None:
        return []
    out: list[dict] = []
    for row in rows[h + 1:]:
        region, y, typ, gw = row[0], _year(row[1]), row[2], _num(row[3])
        if not region or y is None or not typ or gw is None:
            continue
        region_l, typ_l = str(region).strip(), _clean_label(typ)
        out.append(_emit(f"lbnl_queue:active_region_type:{_slug(region_l)}:{_slug(typ_l)}", y, gw,
                         unit="GW", metric="queue_active_capacity",
                         title=f"Active queue capacity — {region_l} / {typ_l}",
                         region=region_l, resource_type=typ_l))
    return out


def parse_queues_vs_installed(rows: list[tuple]) -> list[dict]:
    """09 — queued GW vs installed GW by type, for the snapshot years present (2010 & 2024)."""
    h = _find_header(rows, {"Type", "Capacity (GW)", "Source", "Year"})
    if h is None:
        return []
    out: list[dict] = []
    for row in rows[h + 1:]:
        typ, gw, src, y = row[0], _num(row[1]), str(row[2] or "").strip().lower(), _year(row[3])
        if not typ or gw is None or y is None or src not in ("queues", "installed"):
            continue
        typ_l = _clean_label(typ)
        metric = "queue_capacity_snapshot" if src == "queues" else "installed_capacity"
        out.append(_emit(f"lbnl_queue:{src}_by_type:{_slug(typ_l)}", y, gw, unit="GW",
                         metric=metric,
                         title=f"{'Queued' if src == 'queues' else 'Installed'} capacity — {typ_l}",
                         resource_type=typ_l, source=src))
    return out


def parse_ia_throughput(rows: list[tuple]) -> list[dict]:
    """18 — interconnection-agreements executed per year × region: GW + count (queue THROUGHPUT)."""
    h = _find_header(rows, {"Region", "IA-executed Year", "Capacity (GW)", "Count of IAs"})
    if h is None:
        return []
    out: list[dict] = []
    for row in rows[h + 1:]:
        region, y, gw, cnt = row[0], _year(row[1]), _num(row[2]), _num(row[3])
        if not region or y is None:
            continue
        region_l = str(region).strip()
        if gw is not None:
            out.append(_emit(f"lbnl_queue:ia_throughput_gw:{_slug(region_l)}", y, gw, unit="GW",
                             metric="ia_executed_capacity",
                             title=f"Interconnection agreements executed — {region_l} (capacity)",
                             region=region_l))
        if cnt is not None:
            out.append(_emit(f"lbnl_queue:ia_throughput_count:{_slug(region_l)}", y, cnt,
                             unit="agreements", metric="ia_executed_count",
                             title=f"Interconnection agreements executed — {region_l} (count)",
                             region=region_l))
    return out


def parse_operational_trend(rows: list[tuple]) -> list[dict]:
    """19 — GW reaching commercial operation per year, from two independent sources: the LBNL queue
    data and EIA. Header `Online Year | Capacity Online (Queue Data) (GW) | Capacity Online (EIA)
    (GW)`. We map each value column by whichever source token its header carries."""
    h = None
    for i, row in enumerate(rows[:30]):
        cells = [str(c).strip().lower() if c is not None else "" for c in row]
        if cells and cells[0] == "online year" and any("capacity online" in c for c in cells[1:]):
            h, hdr = i, row
            break
    if h is None:
        return []
    src_by_col: dict[int, str] = {}
    for ci, c in enumerate(hdr):
        if ci == 0 or c is None:
            continue
        label = str(c).lower()
        if "eia" in label:
            src_by_col[ci] = "eia"
        elif "queue" in label:
            src_by_col[ci] = "queue_data"
    out: list[dict] = []
    for row in rows[h + 1:]:
        y = _year(row[0])
        if y is None:
            continue
        for col, src in src_by_col.items():
            v = _num(row[col]) if len(row) > col else None
            if v is not None:
                out.append(_emit(f"lbnl_queue:operational_online:{src}", y, v, unit="GW",
                                 metric="capacity_online",
                                 title=f"Capacity reaching commercial operation per year ({src.replace('_', ' ')})",
                                 source=src))
    return out


def parse_withdrawn_trend(rows: list[tuple]) -> list[dict]:
    """20 — count + GW withdrawn per year (the withdrawal channel: pipeline that does NOT clear)."""
    h = _find_header(rows, {"Withdrawn Year", "n", "Capacity (GW)"})
    if h is None:
        return []
    out: list[dict] = []
    for row in rows[h + 1:]:
        y = _year(row[0])
        if y is None:
            continue
        n, gw = _num(row[1]), _num(row[2])
        if n is not None:
            out.append(_emit("lbnl_queue:withdrawn:count", y, n, unit="projects",
                             metric="queue_withdrawn_count",
                             title="Projects withdrawn from queues per year (count)"))
        if gw is not None:
            out.append(_emit("lbnl_queue:withdrawn:gw", y, gw, unit="GW",
                             metric="queue_withdrawn_capacity",
                             title="Capacity withdrawn from queues per year"))
    return out


_STATUS_COLS = ("Active", "Operational", "Withdrawn", "Suspended")


def parse_completion_trend(rows: list[tuple]) -> list[dict]:
    """21 — by request-cohort year: count AND MW of each final status (active/operational/withdrawn/
    suspended). Two side-by-side blocks ('Count by Status', 'Capacity (MW) by Status') under one
    header row carrying `Request Year` twice — locate each block's columns by position."""
    # the merged header row contains the status labels; find the row with two 'Request Year' cells.
    h = None
    for i, row in enumerate(rows[:30]):
        ry = [j for j, c in enumerate(row) if str(c).strip().lower() == "request year"]
        if len(ry) >= 2 and any(str(c).strip().lower() == "active" for c in row):
            h, ry_cols = i, ry
            break
    if h is None:
        return []
    hdr = rows[h]
    # map each status to its column in the count block (left) and the capacity block (right)
    count_cols = {s: _col_index_after(hdr, s, ry_cols[0]) for s in _STATUS_COLS}
    cap_cols = {s: _col_index_after(hdr, s, ry_cols[1]) for s in _STATUS_COLS}
    out: list[dict] = []
    for row in rows[h + 1:]:
        y = _year(row[ry_cols[0]]) if len(row) > ry_cols[0] else None
        if y is None:
            continue
        for s in _STATUS_COLS:
            ci = count_cols.get(s)
            if ci is not None and len(row) > ci:
                v = _num(row[ci])
                if v is not None:
                    out.append(_emit(f"lbnl_queue:completion_trend_count:{s.lower()}", y, v,
                                     unit="projects", metric="queue_status_count",
                                     title=f"Requests by final status per cohort year — {s} (count)",
                                     status=s.lower()))
            mi = cap_cols.get(s)
            if mi is not None and len(row) > mi:
                v = _num(row[mi])
                if v is not None:
                    out.append(_emit(f"lbnl_queue:completion_trend_mw:{s.lower()}", y, v,
                                     unit="MW", metric="queue_status_capacity",
                                     title=f"Capacity by final status per cohort year — {s}",
                                     status=s.lower()))
    return out


def _col_index_after(header_row: tuple, name: str, start: int) -> int | None:
    """First column at index > start whose header equals `name` (status blocks repeat labels)."""
    target = name.strip().lower()
    for i in range(start + 1, len(header_row)):
        c = header_row[i]
        if c is not None and str(c).strip().lower() == target:
            return i
    return None


def _parse_status_by_dim(rows: list[tuple], dim_header: str, dim_field: str, series_prefix: str,
                         title_dim: str) -> list[dict]:
    """22/23 — cross-sectional MW + count by final status, split by a dimension (type or region).
    Two blocks ('Capacity (MW) by Status', 'Count by Status') under a header carrying the dim label
    twice. Snapshot, no per-row year → carries the dataset's data-through year as the date."""
    h = None
    for i, row in enumerate(rows[:30]):
        dims = [j for j, c in enumerate(row) if str(c).strip().lower() == dim_header.lower()]
        if len(dims) >= 2 and any(str(c).strip().lower() == "active" for c in row):
            h, dim_cols = i, dims
            break
    if h is None:
        return []
    hdr = rows[h]
    cap_cols = {s: _col_index_after(hdr, s, dim_cols[0]) for s in _STATUS_COLS}
    cnt_cols = {s: _col_index_after(hdr, s, dim_cols[1]) for s in _STATUS_COLS}
    y = DATA_THROUGH_YEAR
    out: list[dict] = []
    for row in rows[h + 1:]:
        key = row[dim_cols[0]] if len(row) > dim_cols[0] else None
        if not key or not str(key).strip():
            continue
        key_l = _clean_label(key)
        for s in _STATUS_COLS:
            ci = cap_cols.get(s)
            if ci is not None and len(row) > ci:
                v = _num(row[ci])
                if v is not None:
                    out.append(_emit(f"{series_prefix}_mw:{_slug(key_l)}:{s.lower()}", y, v,
                                     unit="MW", metric="queue_status_capacity",
                                     title=f"Capacity by status — {title_dim} {key_l} / {s}",
                                     data_year=DATA_THROUGH_YEAR, status=s.lower(), **{dim_field: key_l}))
            ki = cnt_cols.get(s)
            if ki is not None and len(row) > ki:
                v = _num(row[ki])
                if v is not None:
                    out.append(_emit(f"{series_prefix}_count:{_slug(key_l)}:{s.lower()}", y, v,
                                     unit="projects", metric="queue_status_count",
                                     title=f"Count by status — {title_dim} {key_l} / {s}",
                                     data_year=DATA_THROUGH_YEAR, status=s.lower(), **{dim_field: key_l}))
    return out


def parse_completion_by_type(rows: list[tuple]) -> list[dict]:
    return _parse_status_by_dim(rows, "Type", "resource_type",
                                "lbnl_queue:completion_by_type", "type")


def parse_completion_by_region(rows: list[tuple]) -> list[dict]:
    return _parse_status_by_dim(rows, "Region", "region",
                                "lbnl_queue:completion_by_region", "region")


def parse_time_in_queue(rows: list[tuple]) -> list[dict]:
    """35 — median (+p25/p75) months from interconnection request to commercial operation, by
    in-service year (the typical-time-in-queue series). Tidy block `In-Service Year | n | mean |
    p25 | Median | p75`."""
    h = None
    for i, row in enumerate(rows[:30]):
        cells = [str(c).strip().lower() if c is not None else "" for c in row]
        if "in-service year" in cells and "median" in cells and "p25" in cells:
            h = i
            break
    if h is None:
        return []
    hdr = rows[h]
    iy = _col_index(hdr, "In-Service Year")
    cols = {k: _col_index(hdr, k) for k in ("n", "mean", "p25", "Median", "p75")}
    if iy is None or cols["Median"] is None:
        return []
    out: list[dict] = []
    for row in rows[h + 1:]:
        y = _year(row[iy]) if len(row) > iy else None
        if y is None:
            continue
        for label, stat, unit in (("Median", "median", "months"), ("p25", "p25", "months"),
                                  ("p75", "p75", "months"), ("mean", "mean", "months"),
                                  ("n", "n", "projects")):
            ci = cols.get(label)
            if ci is None or len(row) <= ci:
                continue
            v = _num(row[ci])
            if v is None:
                continue
            metric = "queue_duration_ir_to_cod" if label != "n" else "queue_duration_sample_n"
            out.append(_emit(f"lbnl_queue:time_in_queue_ir_to_cod:{stat}", y, v, unit=unit,
                             metric=metric,
                             title=f"Time in queue (request → commercial operation) — {stat}",
                             statistic=stat))
    return out


# (sheet name, parser). Sheet names match the workbook's "NN. Title" tab labels for this vintage.
TAB_PARSERS: list[tuple[str, Callable[[list[tuple]], list[dict]]]] = [
    ("05. Annual Requests", parse_annual_requests),
    ("06. Active Capacity by Year", parse_active_by_cohort),
    ("07. Active Capacity by Type", parse_active_by_type),
    ("08. Active Cap. Region+Type", parse_active_region_type),
    ("09. Queues vs. Installed", parse_queues_vs_installed),
    ("18. IA Throughput by Region", parse_ia_throughput),
    ("19. Operational Volume Trend", parse_operational_trend),
    ("20. Withdrawn Volume Trend", parse_withdrawn_trend),
    ("21. Completion Rate Trend", parse_completion_trend),
    ("22. Comp. Rate Gen Type", parse_completion_by_type),
    ("23. Comp. Rate Region", parse_completion_by_region),
    ("35. IR to COD - all", parse_time_in_queue),
]


def _write_jsonl_atomic(rows: list[dict]) -> None:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for o in rows:
            f.write(json.dumps(o, ensure_ascii=False, sort_keys=True) + "\n")
    tmp.replace(OUT_PATH)


def collect(*, log: Callable[[str], Any] = print) -> list[dict]:
    """Download the LBNL queue workbook, parse every targeted summary tab, write the jsonl.

    $0, keyless. Never fabricates: a failed download preserves the existing file; a tab whose layout
    has shifted simply contributes nothing (logged), it never sinks the run.
    """
    raw = _fetch_bytes(DATA_FILE_URL)
    if raw is None:
        log(f"download failed ({DATA_FILE_URL}); preserving existing {OUT_PATH}")
        return []
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
        sst = _shared_strings(zf)
        targets = _sheet_targets(zf)
    except Exception as exc:  # noqa: BLE001
        log(f"workbook unreadable ({exc}); preserving existing {OUT_PATH}")
        return []

    sheet_lookup = {name.strip().lower(): (name, path) for name, path in targets.items()}
    all_obs: list[dict] = []
    for tab_name, parser in TAB_PARSERS:
        hit = sheet_lookup.get(tab_name.strip().lower())
        if hit is None:
            log(f"  - tab not found: {tab_name!r} (skipped)")
            continue
        _real, path = hit
        try:
            rows = _read_sheet_rows(zf, path, sst)
            obs = parser(rows)
        except Exception as exc:  # noqa: BLE001 — one bad tab must never sink the run
            log(f"  - {tab_name}: parse error ({exc}); skipped")
            continue
        if obs:
            years = sorted({o["date"][:4] for o in obs})
            n_series = len({o["series_id"] for o in obs})
            log(f"  + {tab_name:<32} {years[0]}–{years[-1]}  {len(obs):4d} obs / {n_series} series")
        else:
            log(f"  - {tab_name}: no observations parsed (layout shift?)")
        all_obs.extend(obs)

    if not all_obs:
        log(f"\nno observations parsed; preserved existing {OUT_PATH}")
        return []
    all_obs.sort(key=lambda o: (o["series_id"], o["date"]))
    _write_jsonl_atomic(all_obs)
    log(f"\nwrote {len(all_obs)} observations → {OUT_PATH}")
    return all_obs


if __name__ == "__main__":
    print("LBNL 'Queued Up' full interconnection-queue dataset (keyless, emp.lbl.gov):")
    observations = collect()
    if not observations:
        print("\nNO observations collected — workbook unreachable/unparsed (no data written).")
    else:
        series = sorted({o["series_id"] for o in observations})
        dates = sorted({o["date"] for o in observations})
        print(f"\nrows: {len(observations)}   series: {len(series)}   "
              f"date span: {dates[0]} … {dates[-1]}")
        print("\n3 sample observations:")
        for o in observations[:3]:
            print("  " + json.dumps(o, ensure_ascii=False, sort_keys=True))
