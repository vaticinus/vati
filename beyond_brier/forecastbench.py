"""Load the public, leak-free ForecastBench slice into the long edge format.

Scope (the honest bit, verbatim from the paper): public ForecastBench exposes
*raw per-question* forecasts only for the **2024-07-21** round and only for
**human** forecasters (40 superforecasters + 500 public). Per-question LLM
forecasts are not public and the leaderboard's ``Dataset`` column is a
multi-round fixed-effect aggregate, so the LLM board cannot be recomputed here.

Two tracks:

* **market** (manifold, metaculus, polymarket, infer): ``freeze_datetime_value``
  is a genuine probability prior in [0, 1] -> use it.
* **data** (acled, fred, dbnomics, wikipedia, yfinance): ``freeze_datetime_value``
  is a raw *series level*, not a probability. The defensible prior for a
  "will it go up?" question is the random-walk baseline ``p_ref = 0.5``. On a
  constant prior the edge ranking is *identical* to Brier by construction -- this
  track is the metric's identity check, not a result.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict

import numpy as np
import pandas as pd

MARKET = {"manifold", "metaculus", "polymarket", "infer"}
DATA = {"acled", "fred", "dbnomics", "wikipedia", "yfinance"}

DEFAULT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "forecastbench")
ROUND = "2024-07-21"


def _load_json(path):
    with open(path) as f:
        return json.load(f)


def load_round(data_dir: str = DEFAULT_DIR) -> pd.DataFrame:
    """Return the long table for the 2024-07-21 round.

    Columns: ``forecaster, question, source, track, p_f, p_ref, y,
    resolution_date``. Each forecaster id is ``"<super|public>:<user_id>"``.
    Files expected in ``data_dir``: ``q.json``, ``r.json``, ``human_super.json``,
    ``human_public.json`` (fetch them with ``python -m data.fetch``).
    """
    need = ["q.json", "r.json", "human_super.json", "human_public.json"]
    miss = [f for f in need if not os.path.exists(os.path.join(data_dir, f))]
    if miss:
        raise FileNotFoundError(
            f"missing {miss} in {data_dir}. Run `python -m data.fetch` to download "
            "the public ForecastBench files.")

    qset = _load_json(os.path.join(data_dir, "q.json"))["questions"]
    rset = _load_json(os.path.join(data_dir, "r.json"))["resolutions"]

    # reference prior per single-question id (market freeze value)
    p_ref_market = {}
    for q in qset:
        if isinstance(q["id"], str):
            try:
                p_ref_market[q["id"]] = float(q["freeze_datetime_value"])
            except (ValueError, TypeError, KeyError):
                pass

    # resolutions: single ids only, resolved
    res_by_id = defaultdict(list)
    res_by_key = {}
    for r in rset:
        if isinstance(r["id"], str) and r.get("resolved") is True and r.get("resolved_to") is not None:
            y = float(r["resolved_to"])
            res_by_id[r["id"]].append(y)
            res_by_key[(r["id"], r["resolution_date"])] = y

    rows = []
    for tag, fname in [("super", "human_super.json"), ("public", "human_public.json")]:
        d = _load_json(os.path.join(data_dir, fname))
        for f in d["forecasts"]:
            src = f["source"]
            qid = f["id"]
            is_mkt = src in MARKET
            p_ref = p_ref_market.get(qid, np.nan) if is_mkt else 0.5
            if is_mkt:
                cand = res_by_id.get(qid, [])
                y = cand[0] if cand else np.nan
            else:
                y = res_by_key.get((qid, f.get("resolution_date")), np.nan)
            rows.append({
                "forecaster": f"{tag}:{f.get('user_id')}",
                "tag": tag,
                "question": qid,
                "source": src,
                "track": "market" if is_mkt else ("data" if src in DATA else "other"),
                "p_f": f.get("forecast"),
                "p_ref": p_ref,
                "y": y,
                "resolution_date": f.get("resolution_date"),
            })

    df = pd.DataFrame(rows)
    # keep valid probabilities and binary outcomes only
    fc_ok = df["p_f"].notna() & (df["p_f"] >= -0.01) & (df["p_f"] <= 1.01)
    df.loc[fc_ok, "p_f"] = df.loc[fc_ok, "p_f"].clip(0, 1)
    df = df[fc_ok & df["p_ref"].notna() & df["y"].isin([0.0, 1.0])].copy()
    # data-track questions repeat across horizons -> make the question key unique per horizon
    df.loc[df.track == "data", "question"] = (
        df.loc[df.track == "data", "question"].astype(str) + "|" + df.loc[df.track == "data", "resolution_date"].astype(str))
    return df.reset_index(drop=True)
