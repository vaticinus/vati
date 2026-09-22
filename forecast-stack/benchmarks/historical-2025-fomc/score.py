"""Score the registered historical cohort; no model calls or outcome imputation."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def summarize(values: list[tuple[float, int]], planned: int) -> dict:
    losses = [(p-y)**2 for p, y in values]
    logs = [-math.log(p if y else 1-p) if (p if y else 1-p) > 0 else math.inf for p, y in values]
    n = len(values)
    brier = sum(losses)/n if n else None
    return {
        "issued": n, "planned": planned, "coverage": n/planned,
        "brier_issued_only": brier,
        "brier_skill_vs_half": 1-brier/0.25 if brier is not None else None,
        "all_case_brier_bounds": [sum(losses)/planned, (sum(losses)+planned-n)/planned],
        "log_loss": ("Infinity" if any(math.isinf(v) for v in logs) else sum(logs)/n) if n else None,
        "accuracy_at_half_ties_no": sum((p > 0.5) == bool(y) for p, y in values)/n if n else None,
        "hypothetical_utility_issued_only": {
            str(t): {"actions": sum(p > t for p, _ in values),
                     "mean": sum(y-t if p > t else 0 for p, y in values)/n if n else None}
            for t in (0.25, 0.5, 0.75)
        },
    }


def score(cohort: dict, protocol: dict, resolutions: dict, issued: dict) -> dict:
    ids = [c["id"] for c in cohort["cases"]]
    outcomes = {r["id"]: r["outcome"] for r in resolutions["records"]}
    if len(ids) != len(set(ids)) or len(outcomes) != len(resolutions["records"]) or set(outcomes) != set(ids):
        raise ValueError("Cohort and resolution keys must match uniquely")
    if any(type(y) is not int or y not in (0, 1) for y in outcomes.values()):
        raise ValueError("Only resolved binary outcomes may enter this scorer")
    indexed = {}
    for row in issued["rows"]:
        key = (row["model"], row["arm"], row["id"])
        if key in indexed or key[0] not in protocol["models"] or key[1] not in protocol["arms"] or key[2] not in outcomes:
            raise ValueError("Unexpected or duplicate forecast row")
        p = row.get("probability")
        if p is not None and (type(p) not in (int, float) or not math.isfinite(p) or not 0 <= p <= 1):
            raise ValueError("Invalid probability; do not silently score it")
        if p is not None and row["status"] != "issued":
            raise ValueError("Failed rows cannot carry scored probabilities")
        indexed[key] = p
    groups, pairs = [], []
    for model in protocol["models"]:
        for arm in protocol["arms"]:
            values = [(indexed[(model, arm, i)], outcomes[i]) for i in ids if indexed.get((model, arm, i)) is not None]
            groups.append({"model": model, "arm": arm, **summarize(values, len(ids))})
        paired = [i for i in ids if all(indexed.get((model, a, i)) is not None for a in ("direct", "harness"))]
        differences = [(indexed[(model, "harness", i)]-outcomes[i])**2-(indexed[(model, "direct", i)]-outcomes[i])**2 for i in paired]
        pairs.append({"model": model, "complete_pairs": len(paired), "mean_brier_harness_minus_direct": sum(differences)/len(differences) if differences else None, "per_event_differences": dict(zip(paired, differences))})
    return {
        "cohort": cohort["study"], "groups": groups, "paired_comparisons": pairs,
        "baselines": [{"probability": p, **summarize([(p, outcomes[i]) for i in ids], len(ids))} for p in protocol["baseline_probabilities"]],
        "limits": "Descriptive retrospective replay, eight correlated meetings in one policy year. No independent-cluster confidence interval, calibration claim, cross-domain readiness grade or verified served-weight attestation.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issued", type=Path, default=Path(__file__).with_name("issued.json"))
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    root = Path(__file__).parent
    load = lambda p: json.loads(p.read_text())
    result = score(load(root/"cohort.json"), load(root/"protocol.json"), load(root/"resolutions.json"), load(args.issued))
    text = json.dumps(result, indent=2, allow_nan=False)+"\n"
    if args.out:
        args.out.write_text(text)
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
