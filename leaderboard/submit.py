"""The honest, recomputable leaderboard.

A forecaster earns a place by the information it adds *over the market price*, not
by its Brier. Submit a forecast set, get scored, get ranked. The board is a flat
JSON file anyone can reproduce from the same inputs -- no hidden state.

Submission format (CSV, one row per resolved question)::

    question,p_f,p_ref,y
    BTC-100k,0.30,0.42,0
    fed-cut-sep,0.65,0.58,1
    ...

* ``p_f``  : your probability for the YES outcome.
* ``p_ref``: the freely-available prior at the freeze time (market price /
  crowd median). On data-source questions with no market, use 0.5.
* ``y``    : the resolved outcome, 0 or 1.

Commands
--------
    python -m leaderboard.submit add  --name "GPT-5 (zero-shot)" --kind llm  --forecasts mine.csv
    python -m leaderboard.submit add  --name "My bot"           --kind bot  --forecasts mine.csv
    python -m leaderboard.submit render          # rebuild LEADERBOARD.md
    python -m leaderboard.submit list

The point: a model that just *copies the price* lands at edge ~ 0, no matter how
good its Brier looks. Only independent information moves you up.
"""
from __future__ import annotations

import argparse
import json
import os

import pandas as pd

from beyond_brier import marginal_edge, bootstrap_test, encompassing_regression

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD = os.path.join(HERE, "board.json")
MD = os.path.join(os.path.dirname(HERE), "LEADERBOARD.md")
KINDS = {"llm", "bot", "human", "market", "ensemble"}


def _load():
    if not os.path.exists(BOARD):
        return {"entries": []}
    with open(BOARD) as f:
        return json.load(f)


def _save(board):
    with open(BOARD, "w") as f:
        json.dump(board, f, indent=2)


def score_submission(csv_path: str, score: str = "brier") -> dict:
    df = pd.read_csv(csv_path)
    for c in ("p_f", "p_ref", "y"):
        if c not in df.columns:
            raise ValueError(f"submission missing column {c!r} (need p_f, p_ref, y)")
    df = df.dropna(subset=["p_f", "p_ref", "y"])
    er = marginal_edge(df["p_f"], df["p_ref"], df["y"], score=score)
    lo, hi, p = bootstrap_test(er.per_question)
    rec = {
        "n": er.n,
        "edge": round(er.edge, 5),
        "edge_lo": round(lo, 5),
        "edge_hi": round(hi, 5),
        "boot_p": round(p, 5),
        "mean_brier": round(float(((df["p_f"] - df["y"]) ** 2).mean()), 5),
        "score": score,
    }
    # priced/unpriced split when the prior actually varies
    try:
        enc = encompassing_regression(df["p_f"], df["p_ref"], df["y"], question_id=df.get("question"))
        rec["b_fc"] = round(enc.b_fc, 4)
        rec["b_fc_p"] = round(enc.p_fc, 5)
        rec["unpriced"] = enc.b_fc > 0 and enc.p_fc < 0.05
    except Exception:
        rec["unpriced"] = None
    return rec


def add(args):
    if args.kind not in KINDS:
        raise SystemExit(f"--kind must be one of {sorted(KINDS)}")
    rec = score_submission(args.forecasts, score=args.score)
    rec.update({"name": args.name, "kind": args.kind, "track": args.track,
                "date": args.date, "notes": args.notes or ""})
    board = _load()
    board["entries"] = [e for e in board["entries"] if e["name"] != args.name]
    board["entries"].append(rec)
    _save(board)
    print(f"added {args.name!r}: edge={rec['edge']:+.5f} "
          f"[{rec['edge_lo']:+.5f}, {rec['edge_hi']:+.5f}]  (n={rec['n']})")
    render(args)


def _rows_sorted(board):
    return sorted(board["entries"], key=lambda e: e["edge"], reverse=True)


def render(args=None):
    board = _load()
    rows = _rows_sorted(board)
    lines = [
        "# The honest forecasting leaderboard",
        "",
        "Ranked by **marginal edge over the market price** (Brier units, higher = better).",
        "A forecaster that merely copies the price scores ~0 here regardless of its raw Brier.",
        "Everything is recomputable: `python -m leaderboard.submit render`.",
        "",
        "| # | Forecaster | Kind | Edge | 95% CI | Mean Brier | Unpriced? | N |",
        "|---|---|---|---:|---|---:|:---:|---:|",
    ]
    for i, e in enumerate(rows, 1):
        ci = f"[{e['edge_lo']:+.4f}, {e['edge_hi']:+.4f}]"
        up = {True: "yes", False: "no", None: "—"}.get(e.get("unpriced"), "—")
        lines.append(
            f"| {i} | {e['name']} | {e['kind']} | {e['edge']:+.4f} | {ci} | "
            f"{e.get('mean_brier', float('nan')):.4f} | {up} | {e['n']} |")
    lines += [
        "",
        "*Edge = mean of `S(price, y) - S(forecast, y)` per question. "
        "Unpriced? = the forecast carries signal beyond the price in a "
        "forecast-encompassing logit (b_fc > 0, p < 0.05).*",
        "",
    ]
    with open(MD, "w") as f:
        f.write("\n".join(lines))
    print(f"wrote {MD} ({len(rows)} entries)")


def list_entries(args=None):
    for e in _rows_sorted(_load()):
        print(f"  {e['edge']:+.5f}  {e['name']}  ({e['kind']}, n={e['n']})")


def main():
    ap = argparse.ArgumentParser(prog="leaderboard.submit")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="score a forecast CSV and add it to the board")
    a.add_argument("--name", required=True)
    a.add_argument("--forecasts", required=True, help="CSV with p_f, p_ref, y (and optional question)")
    a.add_argument("--kind", default="bot")
    a.add_argument("--track", default="market")
    a.add_argument("--score", default="brier", choices=["brier", "log"])
    a.add_argument("--date", default="")
    a.add_argument("--notes", default="")
    a.set_defaults(func=add)

    sub.add_parser("render", help="rebuild LEADERBOARD.md").set_defaults(func=render)
    sub.add_parser("list", help="print the board").set_defaults(func=list_entries)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
