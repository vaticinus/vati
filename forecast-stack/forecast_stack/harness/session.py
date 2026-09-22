"""Record one actual Codex-session judgment and an explicitly labelled baseline.

No model/provider calls. Hashes establish artifact integrity, not factual truth,
independent research, or a leakage-free model knowledge boundary.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import re
import shutil
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path

from . import ledger
from .schemas import Question, VerifiedEvidenceAtom, ForecastRecord, canonical_hash


def _text(value, name, *, words=1):
    if not isinstance(value, str) or len(value.split()) < words:
        raise ValueError(f"{name} requires substantive text ({words}+ words)")
    if re.search(r"\b(TODO|TBD|placeholder|lorem ipsum)\b|^(Thinking|Fetching task|Deploying researchers)", value.strip(), re.I):
        raise ValueError(f"{name} contains unresolved placeholder output")
    return value


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("dates must use YYYY-MM-DD")
    return date.fromisoformat(value)


def _time(value):
    if not isinstance(value, str) or "T" not in value:
        raise ValueError("timestamps require an ISO date, time, and timezone")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timestamps require a timezone")
    return result.astimezone(timezone.utc)


def _prob(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError("probability must be a finite number in [0,1], not bool")
    return float(value)


def _finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("nonfinite numeric input")
    if isinstance(value, dict):
        for item in value.values():
            _finite(item)
    elif isinstance(value, list):
        for item in value:
            _finite(item)


def _match(payload, key, expected):
    if key in payload and payload[key] != expected:
        raise ValueError(f"{key} does not match its referenced content")
    payload[key] = expected


def validate_session(payload: dict, *, now: datetime | None = None) -> dict:
    """Normalize and validate without any file or network I/O.

    `source_checked` is the author's provenance attestation, not a tool verdict.
    Omitted hashes are sealed; a supplied mismatch is rejected, never repaired.
    """
    if not isinstance(payload, dict):
        raise ValueError("session input must be an object")
    _finite(payload)
    json.dumps(payload, allow_nan=False)  # Reject non-JSON inputs before any writes.
    data = copy.deepcopy(payload)
    _text(data["run_id"], "run_id")
    recorded = _time(data["recorded_at"])
    actual_now = now or datetime.now(timezone.utc)
    if recorded > actual_now:
        raise ValueError("recorded_at cannot be in the future")
    q = Question(**data["question"])
    q.validate_contract(require_complete=True)
    cutoff = _time(q.asof) if "T" in q.asof else _date(q.asof)
    cutoff_date = cutoff.date() if isinstance(cutoff, datetime) else cutoff
    resolution = _time(q.resolution_date) if "T" in q.resolution_date else _date(q.resolution_date)
    future_cutoff = cutoff > recorded if isinstance(cutoff, datetime) else cutoff > recorded.date()
    resolved = resolution <= max(recorded, actual_now) if isinstance(resolution, datetime) else resolution <= max(recorded.date(), actual_now.date())
    if future_cutoff or resolved:
        raise ValueError("cutoff must not be future; resolution must be prospective")
    if q.threshold is not None and (type(q.threshold) not in (int, float) or not math.isfinite(q.threshold)):
        raise ValueError("question threshold must be a finite number")
    if q.contract_hash:
        q.validate_contract(require_hash=True)
    else:
        q.seal_contract_hash()
    data["question"] = asdict(q)
    _match(data, "question_hash", q.contract_hash)
    response, baseline = data["response"], data["baseline"]
    issued, baseline_issued = _time(response["issued_at"]), _time(baseline["issued_at"])
    before_cutoff = issued < cutoff if isinstance(cutoff, datetime) else issued.date() < cutoff
    if not baseline_issued <= issued <= recorded or before_cutoff:
        raise ValueError("require baseline issued <= response issued <= recorded and response >= cutoff date")
    if response.get("status") != "final":
        raise ValueError("response must be final")
    _text(response["model"], "response.model")
    if baseline["type"] not in {"pre_research_same_model", "external"}:
        raise ValueError("baseline type must be pre_research_same_model or external")
    _text(baseline["source"], "baseline.source")
    for member in (baseline, response):
        member["probability"] = _prob(member["probability"])
        _text(member["rationale"], "rationale", words=12)
        _match(member, "question_hash", q.contract_hash)
    for key in ("drivers", "counterevidence", "assumptions", "triggers"):
        if not isinstance(response[key], list) or not response[key]:
            raise ValueError(f"response.{key} requires a nonempty list")
        for item in response[key]:
            _text(item, key)
    if not isinstance(response["uncertainty"], dict) or not response["uncertainty"]:
        raise ValueError("response.uncertainty requires an object")
    if not isinstance(data["evidence"], list) or not data["evidence"]:
        raise ValueError("evidence requires a nonempty list")
    atoms, by_id = [], {}
    for raw in data["evidence"]:
        _match(raw, "question_hash", q.contract_hash)
        provenance = raw["provenance"]
        if provenance.get("verification_status") not in {"unverified", "source_checked"}:
            raise ValueError("declare provenance verification_status")
        for key in ("citation", "excerpt"):
            _text(provenance[key], f"provenance.{key}")
        captured = _time(raw["captured_at"])
        published = _time(raw["published_at"]) if "T" in raw["published_at"] else _date(raw["published_at"])
        publication_date = published.date() if isinstance(published, datetime) else published
        after_cutoff = publication_date > cutoff_date
        if isinstance(cutoff, datetime):
            after_cutoff = published > cutoff if isinstance(published, datetime) else publication_date >= cutoff_date
        if after_cutoff or publication_date > captured.date() or captured > issued:
            raise ValueError("evidence publication/capture violates information boundary")
        if isinstance(published, datetime) and published > captured:
            raise ValueError("publication cannot follow capture")
        if baseline["type"] == "pre_research_same_model" and baseline_issued > captured:
            raise ValueError("pre-research baseline must precede evidence capture")
        _match(raw, "content_hash", canonical_hash({"citation": provenance["citation"], "excerpt": provenance["excerpt"], "snapshot_path": provenance.get("snapshot_path")}))
        atom = VerifiedEvidenceAtom(**raw)
        atom.validate()
        if atom.atom_hash:
            atom.validate(require_hash=True)
        else:
            atom.seal_atom_hash()
        if atom.atom_id in by_id:
            raise ValueError("duplicate evidence atom_id")
        by_id[atom.atom_id] = atom
        atoms.append(atom)
    hashes = {a.atom_hash for a in atoms}
    for atom in atoms:
        refs = atom.depends_on_atom_hashes + atom.contradicts_atom_hashes
        if any(ref not in hashes or ref == atom.atom_hash for ref in refs):
            raise ValueError("evidence references unknown/self atom hashes")
    selected = response["evidence_atom_ids"]
    if not isinstance(selected, list) or not selected or any(not isinstance(x, str) or x not in by_id for x in selected) or len(selected) != len(set(selected)):
        raise ValueError("response must cite unique known evidence_atom_ids")
    data["evidence"] = [asdict(a) for a in atoms]
    _match(data, "evidence_hash", canonical_hash(data["evidence"]))
    _match(response, "evidence_hash", data["evidence_hash"])
    response_hash = canonical_hash({k: v for k, v in response.items() if k != "response_hash"})
    _match(response, "response_hash", response_hash)
    record = ForecastRecord(
        forecast_id=data["run_id"] + ":main", question_hash=q.contract_hash,
        record_type="initial", method_id="codex_session", prior=baseline["probability"],
        p_yes=response["probability"], evidence_delta_hashes=[by_id[x].atom_hash for x in selected],
        dependencies=[], uncertainty=response["uncertainty"], triggers=response["triggers"],
        input_hash=canonical_hash({"question": data["question"], "evidence": data["evidence"], "baseline": baseline}),
        response_hash=response_hash,
    ).seal_forecast_hash().validate(require_hash=True)
    _match(data, "forecast_record", asdict(record))
    return data


def record_session(payload: dict, out_dir: str | Path) -> dict:
    """Validate first, then exclusively create a new run directory; never overwrite."""
    data = validate_session(payload)
    out = Path(out_dir)
    out.mkdir(exist_ok=False)
    try:
        for name, value in (("input", payload), ("question", data["question"]), ("evidence", data["evidence"]), ("baseline", data["baseline"]), ("response", data["response"]), ("forecast", data["forecast_record"])):
            (out / f"{name}.json").write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
        for member in ("baseline", "main"):
            value = data["baseline"] if member == "baseline" else data["response"]
            event_payload = {"response": value}
            if member == "main":
                event_payload.update({"forecast_record": data["forecast_record"], "question": data["question"], "evidence": data["evidence"], "baseline": data["baseline"]})
            ledger.append_forecast_sealed(
                out / "events.jsonl", question_id=data["question_hash"], run_id=data["run_id"],
                member_id=member, probability=value["probability"], timestamp=data["recorded_at"],
                forecast_payload=event_payload,
            )
        ledger.verify_event_chain(out / "events.jsonl")
        (out / "summary.md").write_text(
            f"# {data['question']['operational']}\n\nMain: {data['response']['probability']:.1%}. "
            f"Baseline ({data['baseline']['type']}): {data['baseline']['probability']:.1%}.\n\n"
            + data["response"]["rationale"] + "\n\nOne in-session judgment; baseline is descriptive, not a verified strong benchmark. "
            "Hashes establish content integrity, not factual truth or freedom from model-knowledge leakage. "
            "Source verification labels are author declarations. Full explanation and evidence are in response.json and evidence.json.\n"
        )
    except BaseException:
        shutil.rmtree(out)
        raise
    return data


def paired_scores(path: str | Path) -> dict:
    """Score first sealed pairs issued before resolution; revisions cannot rewrite them.

    The verified ledger embeds original question, evidence, and responses; sibling
    JSON files are convenient copies, not the scorer's authoritative input.
    """
    rows = ledger.load_event_ledger(path, verify=True)
    resolutions, forecasts = {}, {}
    for row in rows:
        key = (row["question_id"], row["run_id"])
        if row["event_type"] == "resolution_recorded":
            resolutions[key] = row
        elif row["event_type"] == "forecast_sealed" and row["member_id"] in {"baseline", "main"}:
            forecasts.setdefault(key, {}).setdefault(row["member_id"], row)
    pairs = []
    for key, resolution in resolutions.items():
        members = forecasts.get(key, {})
        if set(members) != {"baseline", "main"}:
            continue
        resolved = _time(resolution["resolved_at"])
        if any(_time(e["timestamp"]) >= resolved or _time(e.get("forecast_payload", {}).get("response", {}).get("issued_at", e["timestamp"])) >= resolved for e in members.values()):
            continue
        scores = {name: (event["probability"] - resolution["outcome"]) ** 2 for name, event in members.items()}
        pairs.append({"question_id": key[0], "run_id": key[1], "baseline_brier": scores["baseline"], "main_brier": scores["main"], "delta_main_minus_baseline": scores["main"] - scores["baseline"]})
    return {"n_pairs": len(pairs), "pairs": pairs, "mean_delta_main_minus_baseline": sum(p["delta_main_minus_baseline"] for p in pairs) / len(pairs) if pairs else None}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "record", "score"))
    parser.add_argument("--input", type=Path)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--ledger", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "score":
            if not args.ledger:
                parser.error("score requires --ledger")
            result = paired_scores(args.ledger)
        else:
            if not args.input or (args.command == "record" and not args.out_dir):
                parser.error("requires --input and record requires --out-dir")
            payload = json.loads(args.input.read_text())
            data = record_session(payload, args.out_dir) if args.command == "record" else validate_session(payload)
            result = {"valid": True, "run_id": data["run_id"], "question_hash": data["question_hash"], "forecast_hash": data["forecast_record"]["forecast_hash"], "n_evidence": len(data["evidence"])}
    except (ValueError, TypeError, KeyError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
