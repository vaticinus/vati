"""Append-only forecast decision ledger, event chain, and binary scoring.

The legacy decision ledger records what the system knew at submission time:
final forecast, shadow forecasts, anchors, model route, and calibration tag.
The event ledger below extends the same JSONL/stable-hash convention into a
tamper-evident chain for prospective runtime events. Outcomes are separate
events, never mutations of forecast records.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from forecast_stack.config import DATA_DIR
from typing import Any, Iterable

DEFAULT_LEDGER = DATA_DIR / "forecasting" / "decisions.jsonl"

EVENT_SCHEMA_VERSION = "forecast_event_ledger.v1"
FORECAST_VARIANT_INDEPENDENT = "independent"
FORECAST_VARIANT_CROWD_AWARE_AGGREGATE = "crowd_aware_aggregate"

EVENT_TYPES = frozenset(
    {
        "forecast_sealed",
        "forecast_revised",
        "resolution_recorded",
        "error_attributed",
    }
)
FORECAST_EVENT_TYPES = frozenset({"forecast_sealed", "forecast_revised"})
ERROR_TAXONOMY = frozenset(
    {
        "contract",
        "missing_information",
        "retrieval_or_source",
        "factual_extraction",
        "evidence_selection_or_dilution",
        "reference_class",
        "causal_model",
        "probability_or_update",
        "aggregation",
        "update_timing",
        "irreducible_surprise",
    }
)


class LedgerIntegrityError(ValueError):
    """Raised when an event ledger fails hash, lineage, or schema checks."""


class LedgerReferenceError(LedgerIntegrityError):
    """Raised when an event references a missing or invalid prior event."""


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (str, int, bool)) or obj is None:
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    return str(obj)


def _canonical_json(record: dict) -> str:
    return json.dumps(_jsonable(record), sort_keys=True, separators=(",", ":"))


def stable_hash(record: dict) -> str:
    """Return the deterministic SHA-256 hash for a JSONL record.

    The hash deliberately excludes only ``record_hash`` so legacy decision rows
    and versioned event rows share the same integrity rule.
    """
    payload = {k: v for k, v in record.items() if k != "record_hash"}
    raw = _canonical_json(payload)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _content_hash(namespace: str, payload: dict) -> str:
    raw = f"{namespace}:{_canonical_json(payload)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _stable_event_id(event: dict) -> str:
    payload = {k: v for k, v in event.items() if k not in {"event_id", "record_hash"}}
    return _content_hash("event", payload)


def _append_jsonl_sync(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(_canonical_json(record) + "\n")
        f.flush()
        os.fsync(f.fileno())


def append_decision(record: dict, path: str | Path | None = None) -> dict:
    """Append one legacy decision record and return the normalized record."""
    path = Path(path or DEFAULT_LEDGER)
    rec = _jsonable(dict(record))
    rec.setdefault("logged_at", datetime.now(timezone.utc).isoformat())
    rec["record_hash"] = stable_hash(rec)
    _append_jsonl_sync(path, rec)
    return rec


def load_decisions(path: str | Path | None = None) -> list[dict]:
    path = Path(path or DEFAULT_LEDGER)
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _require_event_path(path: str | Path | None) -> Path:
    if path is None:
        raise ValueError("event ledger path is required for event-chain APIs")
    return Path(path)


def _utc_iso(value: Any | None = None) -> str:
    if value is None:
        dt = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"invalid UTC timestamp: {value!r}") from exc
    else:
        raise ValueError(f"invalid UTC timestamp: {value!r}")
    if dt.tzinfo is None:
        raise ValueError("event timestamps must be timezone-aware UTC values")
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _require_nonempty(value: Any, field: str) -> Any:
    if value is None or str(value) == "":
        raise ValueError(f"{field} is required")
    return value


def _ref(value: Any) -> str:
    return str(value)


def _event_question_run_key(event: dict) -> tuple[str, str]:
    return (_ref(event.get("question_id")), _ref(event.get("run_id")))


def _event_member_key(event: dict) -> tuple[str, str, str]:
    return (
        _ref(event.get("question_id")),
        _ref(event.get("run_id")),
        _ref(event.get("member_id")),
    )


def _strict_probability(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("probability must be numeric in [0, 1], not bool")
    try:
        p = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid probability: {value!r}") from exc
    if not math.isfinite(p) or p < 0.0 or p > 1.0:
        raise ValueError(f"probability outside [0, 1]: {value!r}")
    return p


def _strict_binary_outcome(value: Any) -> int:
    if isinstance(value, bool):
        return 1 if value else 0
    if type(value) is int and value in (0, 1):
        return int(value)
    raise ValueError(f"resolution outcome must be unambiguous binary 0/1, got {value!r}")


def _normalize_variant(value: Any) -> str:
    raw = str(value or FORECAST_VARIANT_INDEPENDENT).strip()
    aliases = {
        "crowd-aware": FORECAST_VARIANT_CROWD_AWARE_AGGREGATE,
        "crowd-aware aggregate": FORECAST_VARIANT_CROWD_AWARE_AGGREGATE,
        "crowd_aware": FORECAST_VARIANT_CROWD_AWARE_AGGREGATE,
        "crowd_aware_aggregate": FORECAST_VARIANT_CROWD_AWARE_AGGREGATE,
    }
    return aliases.get(raw, raw)


def _forecast_content_hash(event: dict) -> str:
    payload = {
        "event_type": event.get("event_type"),
        "question_id": event.get("question_id"),
        "run_id": event.get("run_id"),
        "member_id": event.get("member_id"),
        "variant": event.get("variant"),
        "probability": event.get("probability"),
        "revision_type": event.get("revision_type"),
        "prior_event_id": event.get("prior_event_id"),
        "prior_probability": event.get("prior_probability"),
        "prior_forecast_hash": event.get("prior_forecast_hash"),
        "forecast_payload": event.get("forecast_payload", event.get("payload")),
    }
    return _content_hash("forecast", payload)


def _resolution_content_hash(event: dict) -> str:
    payload = {
        "question_id": event.get("question_id"),
        "run_id": event.get("run_id"),
        "outcome": event.get("outcome"),
        "resolved_at": event.get("resolved_at"),
        "source": event.get("source"),
    }
    return _content_hash("resolution", payload)


def _attribution_content_hash(event: dict) -> str:
    payload = {
        "question_id": event.get("question_id"),
        "run_id": event.get("run_id"),
        "forecast_event_id": event.get("forecast_event_id"),
        "forecast_hash": event.get("forecast_hash"),
        "resolution_event_id": event.get("resolution_event_id"),
        "resolution_hash": event.get("resolution_hash"),
        "label": event.get("label"),
    }
    return _content_hash("attribution", payload)


def _read_event_rows(path: str | Path | None) -> list[dict]:
    event_path = _require_event_path(path)
    if not event_path.exists():
        return []
    rows: list[dict] = []
    with event_path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise LedgerIntegrityError(f"invalid JSON on event ledger line {line_no}") from exc
            if not isinstance(row, dict):
                raise LedgerIntegrityError(f"event ledger line {line_no} is not an object")
            rows.append(row)
    return rows


def _normalise_event_fields(event: dict, prior_events: list[dict]) -> dict:
    event_type = str(_require_nonempty(event.get("event_type"), "event_type"))
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unsupported event_type: {event_type!r}")
    event["event_type"] = event_type
    event["schema_version"] = event.get("schema_version", EVENT_SCHEMA_VERSION)
    if event["schema_version"] != EVENT_SCHEMA_VERSION:
        raise ValueError(f"unsupported event schema_version: {event['schema_version']!r}")
    event["question_id"] = _require_nonempty(event.get("question_id"), "question_id")
    event["run_id"] = _require_nonempty(event.get("run_id"), "run_id")
    event["timestamp"] = _utc_iso(event.get("timestamp"))

    prior_by_id = {_ref(e.get("event_id")): e for e in prior_events}

    if event_type in FORECAST_EVENT_TYPES:
        if "outcome" in event:
            raise ValueError("forecast outcomes must be recorded as separate resolution_recorded events")
        event["probability"] = _strict_probability(event.get("probability"))
        event["member_id"] = _require_nonempty(event.get("member_id"), "member_id")
        if event_type == "forecast_revised":
            prior_event_id = _require_nonempty(event.get("prior_event_id"), "prior_event_id")
            event["prior_event_id"] = prior_event_id
            prior = prior_by_id.get(_ref(prior_event_id))
            if prior and prior.get("event_type") in FORECAST_EVENT_TYPES:
                if "variant" not in event:
                    event["variant"] = prior.get("variant")
                if "prior_probability" in event and event["prior_probability"] != prior.get("probability"):
                    raise ValueError("prior_probability does not match referenced forecast")
                if "prior_forecast_hash" in event and event["prior_forecast_hash"] != prior.get("forecast_hash"):
                    raise ValueError("prior_forecast_hash does not match referenced forecast")
                event["prior_probability"] = prior.get("probability")
                event["prior_forecast_hash"] = prior.get("forecast_hash")
            event["revision_type"] = str(event.get("revision_type") or "reconciliation").strip().lower()
            _require_nonempty(event["revision_type"], "revision_type")
        event["variant"] = _normalize_variant(event.get("variant"))
        event.setdefault("forecast_hash", _forecast_content_hash(event))

    elif event_type == "resolution_recorded":
        event["outcome"] = _strict_binary_outcome(event.get("outcome"))
        event["resolved_at"] = _utc_iso(event.get("resolved_at", event["timestamp"]))
        event.setdefault("resolution_hash", _resolution_content_hash(event))

    elif event_type == "error_attributed":
        label = event.get("label", event.get("error_label"))
        label = str(_require_nonempty(label, "label"))
        if label not in ERROR_TAXONOMY:
            raise ValueError(f"unsupported error attribution label: {label!r}")
        event["label"] = label
        forecast_event_id = _require_nonempty(event.get("forecast_event_id"), "forecast_event_id")
        resolution_event_id = _require_nonempty(event.get("resolution_event_id"), "resolution_event_id")
        event["forecast_event_id"] = forecast_event_id
        event["resolution_event_id"] = resolution_event_id
        forecast = prior_by_id.get(_ref(forecast_event_id))
        resolution = prior_by_id.get(_ref(resolution_event_id))
        if forecast and forecast.get("event_type") in FORECAST_EVENT_TYPES:
            for field in ("member_id", "variant"):
                if field in event and event[field] != forecast.get(field):
                    raise ValueError(f"{field} does not match referenced forecast")
                event[field] = forecast.get(field)
            event["forecast_probability"] = forecast.get("probability")
            event["forecast_hash"] = forecast.get("forecast_hash")
        if resolution and resolution.get("event_type") == "resolution_recorded":
            event["resolution_outcome"] = resolution.get("outcome")
            event["resolution_hash"] = resolution.get("resolution_hash")
        event.setdefault("attribution_hash", _attribution_content_hash(event))

    return event


def _verify_timestamp_is_utc_z(event: dict) -> None:
    timestamp = event.get("timestamp")
    if not isinstance(timestamp, str) or _utc_iso(timestamp) != timestamp:
        raise LedgerIntegrityError("event timestamp is not normalized UTC")


def _verify_common_event_fields(event: dict, index: int, prev_record_hash: str | None, seen_ids: set[str]) -> None:
    for field in (
        "event_id",
        "question_id",
        "run_id",
        "event_type",
        "schema_version",
        "timestamp",
        "prev_record_hash",
        "record_hash",
    ):
        if field not in event:
            raise LedgerIntegrityError(f"missing required event field: {field}")
    if event.get("schema_version") != EVENT_SCHEMA_VERSION:
        raise LedgerIntegrityError(f"unsupported event schema_version: {event.get('schema_version')!r}")
    if event.get("event_type") not in EVENT_TYPES:
        raise LedgerIntegrityError(f"unsupported event_type: {event.get('event_type')!r}")
    if event.get("chain_index") != index:
        raise LedgerIntegrityError("event chain_index does not match file order")
    if event.get("prev_record_hash") != prev_record_hash:
        raise LedgerIntegrityError("event prev_record_hash does not link to previous record")
    _verify_timestamp_is_utc_z(event)
    event_id = _ref(event.get("event_id"))
    if event_id in seen_ids:
        raise LedgerIntegrityError(f"duplicate event_id: {event_id}")
    expected_event_id = _stable_event_id(event)
    if event.get("event_id") != expected_event_id:
        raise LedgerIntegrityError("event_id does not match stable event content")
    expected_record_hash = stable_hash(event)
    if event.get("record_hash") != expected_record_hash:
        raise LedgerIntegrityError("record_hash does not match event content")


def _verify_forecast_event(event: dict, prior_by_id: dict[str, dict], reconciliation_seen: set[tuple[str, str, str]]) -> None:
    if "outcome" in event:
        raise LedgerIntegrityError("forecast event contains an outcome mutation")
    probability = _strict_probability(event.get("probability"))
    if event.get("probability") != probability:
        raise LedgerIntegrityError("forecast probability is not normalized")
    _require_nonempty(event.get("member_id"), "member_id")
    _require_nonempty(event.get("variant"), "variant")
    if event.get("forecast_hash") != _forecast_content_hash(event):
        raise LedgerIntegrityError("forecast_hash does not match forecast content")

    if event.get("event_type") != "forecast_revised":
        return

    prior_event_id = _ref(_require_nonempty(event.get("prior_event_id"), "prior_event_id"))
    prior = prior_by_id.get(prior_event_id)
    if prior is None:
        raise LedgerReferenceError("forecast revision references missing prior forecast")
    if prior.get("event_type") not in FORECAST_EVENT_TYPES:
        raise LedgerReferenceError("forecast revision prior_event_id is not a forecast event")
    for field in ("question_id", "run_id", "member_id", "variant"):
        if event.get(field) != prior.get(field):
            raise LedgerReferenceError(f"forecast revision {field} does not match prior forecast")
    if event.get("prior_probability") != prior.get("probability"):
        raise LedgerReferenceError("forecast revision did not preserve prior probability")
    if event.get("prior_forecast_hash") != prior.get("forecast_hash"):
        raise LedgerReferenceError("forecast revision did not preserve prior forecast hash")
    revision_type = str(_require_nonempty(event.get("revision_type"), "revision_type")).strip().lower()
    if revision_type == "reconciliation" and _event_member_key(event) in reconciliation_seen:
        raise LedgerReferenceError("only one reconciliation revision is allowed per member")


def _verify_resolution_event(event: dict, resolutions_seen: set[tuple[str, str]]) -> None:
    if type(event.get("outcome")) is not int or event.get("outcome") not in (0, 1):
        raise LedgerIntegrityError("resolution outcome is not normalized unambiguous binary 0/1")
    key = _event_question_run_key(event)
    if key in resolutions_seen:
        raise LedgerReferenceError("duplicate resolution for question/run")
    if event.get("resolution_hash") != _resolution_content_hash(event):
        raise LedgerIntegrityError("resolution_hash does not match resolution content")


def _verify_attribution_event(event: dict, prior_by_id: dict[str, dict]) -> None:
    label = event.get("label")
    if label not in ERROR_TAXONOMY:
        raise LedgerReferenceError(f"unsupported error attribution label: {label!r}")
    resolution = prior_by_id.get(_ref(event.get("resolution_event_id")))
    if resolution is None:
        raise LedgerReferenceError("error attribution references missing resolution")
    if resolution.get("event_type") != "resolution_recorded":
        raise LedgerReferenceError("error attribution resolution_event_id is not a resolution")
    if resolution.get("outcome") not in (0, 1) or type(resolution.get("outcome")) is not int:
        raise LedgerReferenceError("error attribution resolution is not unambiguous binary")
    forecast = prior_by_id.get(_ref(event.get("forecast_event_id")))
    if forecast is None:
        raise LedgerReferenceError("error attribution references missing forecast")
    if forecast.get("event_type") not in FORECAST_EVENT_TYPES:
        raise LedgerReferenceError("error attribution forecast_event_id is not a forecast")
    for field in ("question_id", "run_id"):
        if event.get(field) != resolution.get(field) or event.get(field) != forecast.get(field):
            raise LedgerReferenceError(f"error attribution {field} does not match linked events")
    if "member_id" in event and event.get("member_id") != forecast.get("member_id"):
        raise LedgerReferenceError("error attribution member_id does not match linked forecast")
    if "variant" in event and event.get("variant") != forecast.get("variant"):
        raise LedgerReferenceError("error attribution variant does not match linked forecast")
    if event.get("forecast_hash") != forecast.get("forecast_hash"):
        raise LedgerReferenceError("error attribution forecast_hash does not match linked forecast")
    if event.get("forecast_probability") != forecast.get("probability"):
        raise LedgerReferenceError("error attribution forecast_probability does not match linked forecast")
    if event.get("resolution_hash") != resolution.get("resolution_hash"):
        raise LedgerReferenceError("error attribution resolution_hash does not match linked resolution")
    if event.get("resolution_outcome") != resolution.get("outcome"):
        raise LedgerReferenceError("error attribution resolution_outcome does not match linked resolution")
    if event.get("attribution_hash") != _attribution_content_hash(event):
        raise LedgerIntegrityError("attribution_hash does not match attribution content")


def _verify_event_rows(rows: list[dict]) -> list[dict]:
    prev_record_hash: str | None = None
    seen_ids: set[str] = set()
    prior_by_id: dict[str, dict] = {}
    reconciliation_seen: set[tuple[str, str, str]] = set()
    resolutions_seen: set[tuple[str, str]] = set()

    for index, event in enumerate(rows):
        if not isinstance(event, dict):
            raise LedgerIntegrityError(f"event row {index} is not an object")
        _verify_common_event_fields(event, index, prev_record_hash, seen_ids)
        event_type = event.get("event_type")
        if event_type in FORECAST_EVENT_TYPES:
            if _event_question_run_key(event) in resolutions_seen:
                raise LedgerReferenceError("forecast event appears after resolution for the same question/run")
            _verify_forecast_event(event, prior_by_id, reconciliation_seen)
        elif event_type == "resolution_recorded":
            _verify_resolution_event(event, resolutions_seen)
        elif event_type == "error_attributed":
            _verify_attribution_event(event, prior_by_id)

        event_id = _ref(event.get("event_id"))
        seen_ids.add(event_id)
        prior_by_id[event_id] = event
        if (
            event_type == "forecast_revised"
            and str(event.get("revision_type")).strip().lower() == "reconciliation"
        ):
            reconciliation_seen.add(_event_member_key(event))
        if event_type == "resolution_recorded":
            resolutions_seen.add(_event_question_run_key(event))
        prev_record_hash = event.get("record_hash")
    return rows


def load_event_ledger(path: str | Path, *, verify: bool = True) -> list[dict]:
    """Load versioned event rows from an explicit JSONL path.

    Verification is fail-closed by default and detects row mutation, broken
    hashes, broken/reordered chain links, duplicate event IDs, and invalid
    event references.
    """
    rows = _read_event_rows(path)
    return _verify_event_rows(rows) if verify else rows


def load_events(path: str | Path, *, verify: bool = True) -> list[dict]:
    """Alias for ``load_event_ledger``."""
    return load_event_ledger(path, verify=verify)


def verify_event_chain(path: str | Path) -> dict:
    """Verify an explicit event ledger path and return its chain summary."""
    event_path = _require_event_path(path)
    if not event_path.exists():
        raise LedgerIntegrityError(f"ledger does not exist: {event_path}")
    events = load_event_ledger(path, verify=True)
    head = events[-1]["record_hash"] if events else None
    return {"valid": True, "n": len(events), "head_record_hash": head}


def verify_events(path: str | Path) -> dict:
    """Alias for ``verify_event_chain``."""
    return verify_event_chain(path)


def append_event(path: str | Path, event: dict | None = None, **fields: Any) -> dict:
    """Append one typed event to an explicit event-ledger path.

    The new row links to the current chain head. Existing rows are verified
    before append, so new writes never extend a ledger that is already broken.
    """
    event_path = _require_event_path(path)
    prior_events = load_event_ledger(event_path, verify=True)
    current_head = prior_events[-1]["record_hash"] if prior_events else None

    rec: dict[str, Any] = {}
    if event is not None:
        if not isinstance(event, dict):
            raise TypeError("event must be a dict")
        rec.update(event)
    rec.update(fields)

    provided_prev = rec.pop("prev_record_hash", current_head)
    if provided_prev != current_head:
        raise ValueError("new event prev_record_hash must match current chain head")
    provided_event_id = rec.pop("event_id", None)
    provided_record_hash = rec.pop("record_hash", None)

    rec["prev_record_hash"] = current_head
    rec["chain_index"] = len(prior_events)
    rec = _normalise_event_fields(rec, prior_events)
    rec["event_id"] = _stable_event_id(rec)
    if provided_event_id is not None and provided_event_id != rec["event_id"]:
        raise ValueError("provided event_id does not match stable event content")
    rec["record_hash"] = stable_hash(rec)
    if provided_record_hash is not None and provided_record_hash != rec["record_hash"]:
        raise ValueError("provided record_hash does not match event content")

    _verify_event_rows(prior_events + [rec])
    _append_jsonl_sync(event_path, rec)
    return rec


def append_forecast_sealed(
    path: str | Path,
    *,
    question_id: Any,
    run_id: Any,
    member_id: Any,
    probability: Any,
    variant: str = FORECAST_VARIANT_INDEPENDENT,
    timestamp: Any | None = None,
    forecast_payload: Any | None = None,
    **extra: Any,
) -> dict:
    """Append an initial sealed forecast event."""
    event = dict(extra)
    event.update(
        {
            "event_type": "forecast_sealed",
            "question_id": question_id,
            "run_id": run_id,
            "member_id": member_id,
            "probability": probability,
            "variant": variant,
        }
    )
    if timestamp is not None:
        event["timestamp"] = timestamp
    if forecast_payload is not None:
        event["forecast_payload"] = forecast_payload
    return append_event(path, event)


def append_forecast_revised(
    path: str | Path,
    *,
    question_id: Any,
    run_id: Any,
    member_id: Any,
    probability: Any,
    prior_event_id: Any,
    revision_type: str = "reconciliation",
    variant: str | None = None,
    timestamp: Any | None = None,
    forecast_payload: Any | None = None,
    **extra: Any,
) -> dict:
    """Append a forecast revision preserving the referenced prior state."""
    event = dict(extra)
    event.update(
        {
            "event_type": "forecast_revised",
            "question_id": question_id,
            "run_id": run_id,
            "member_id": member_id,
            "probability": probability,
            "prior_event_id": prior_event_id,
            "revision_type": revision_type,
        }
    )
    if variant is not None:
        event["variant"] = variant
    if timestamp is not None:
        event["timestamp"] = timestamp
    if forecast_payload is not None:
        event["forecast_payload"] = forecast_payload
    return append_event(path, event)


def append_resolution_recorded(
    path: str | Path,
    *,
    question_id: Any,
    run_id: Any,
    outcome: Any,
    timestamp: Any | None = None,
    resolved_at: Any | None = None,
    source: Any | None = None,
    **extra: Any,
) -> dict:
    """Append a separate binary resolution event."""
    event = dict(extra)
    event.update(
        {
            "event_type": "resolution_recorded",
            "question_id": question_id,
            "run_id": run_id,
            "outcome": outcome,
        }
    )
    if timestamp is not None:
        event["timestamp"] = timestamp
    if resolved_at is not None:
        event["resolved_at"] = resolved_at
    if source is not None:
        event["source"] = source
    return append_event(path, event)


def append_error_attributed(
    path: str | Path,
    *,
    question_id: Any,
    run_id: Any,
    forecast_event_id: Any,
    resolution_event_id: Any,
    label: str,
    timestamp: Any | None = None,
    **extra: Any,
) -> dict:
    """Append a closed-taxonomy error attribution linked to a binary resolution."""
    event = dict(extra)
    event.update(
        {
            "event_type": "error_attributed",
            "question_id": question_id,
            "run_id": run_id,
            "forecast_event_id": forecast_event_id,
            "resolution_event_id": resolution_event_id,
            "label": label,
        }
    )
    if timestamp is not None:
        event["timestamp"] = timestamp
    return append_event(path, event)


append_forecast_revision = append_forecast_revised
append_resolution = append_resolution_recorded
append_error_attribution = append_error_attributed


def _events_from_path_or_iterable(path_or_events: str | Path | Iterable[dict]) -> list[dict]:
    if isinstance(path_or_events, (str, Path)):
        return load_event_ledger(path_or_events, verify=True)
    return _verify_event_rows([dict(e) for e in path_or_events])


def score_linked_binary_events(path_or_events: str | Path | Iterable[dict]) -> dict:
    """Score latest linked binary forecasts against separate resolution events.

    Returns ``{variant: {n, brier}}``. Forecast revisions supersede the prior
    forecast for the same question/run/member/variant. Independent forecasts
    and crowd-aware aggregate forecasts stay in separate variant buckets.
    """
    events = _events_from_path_or_iterable(path_or_events)
    resolutions: dict[tuple[str, str], dict] = {}
    forecasts: dict[tuple[str, str, str, str], dict] = {}
    for event in events:
        event_type = event.get("event_type")
        if event_type == "resolution_recorded":
            resolutions[_event_question_run_key(event)] = event
        elif event_type in FORECAST_EVENT_TYPES:
            key = (
                _ref(event.get("question_id")),
                _ref(event.get("run_id")),
                _ref(event.get("member_id")),
                _ref(event.get("variant")),
            )
            forecasts[key] = event

    sums: dict[str, list[float]] = {}
    for forecast in forecasts.values():
        resolution = resolutions.get(_event_question_run_key(forecast))
        if resolution is None:
            continue
        p = _strict_probability(forecast.get("probability"))
        y = float(_strict_binary_outcome(resolution.get("outcome")))
        variant = _ref(forecast.get("variant"))
        sums.setdefault(variant, []).append((p - y) ** 2)
    return {
        name: {"n": len(vals), "brier": sum(vals) / len(vals)}
        for name, vals in sorted(sums.items())
        if vals
    }


score_event_ledger = score_linked_binary_events
score_event_binary_forecasts = score_linked_binary_events
score_binary_event_chain = score_linked_binary_events


def _as_prob(x: Any) -> float | None:
    try:
        p = float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(p):
        return None
    return max(0.0, min(1.0, p))


def score_binary_shadows(records: Iterable[dict]) -> dict:
    """Score final and shadow probabilities on records that carry `outcome`.

    Returns {variant: {n, brier}}. Records without a binary outcome are ignored.
    Versioned event rows are delegated to ``score_linked_binary_events``.
    """
    rows = list(records)
    if rows and all(isinstance(rec, dict) and "event_type" in rec for rec in rows):
        return score_linked_binary_events(rows)

    sums: dict[str, list[float]] = {}
    for rec in rows:
        if rec.get("outcome") not in (0, 1, False, True):
            continue
        y = 1.0 if rec.get("outcome") in (1, True) else 0.0
        candidates = {}
        if "forecast" in rec:
            candidates["final"] = rec.get("forecast")
        candidates.update(rec.get("shadows") or {})
        for name, value in candidates.items():
            p = _as_prob(value)
            if p is None:
                continue
            sums.setdefault(str(name), []).append((p - y) ** 2)
    return {
        name: {"n": len(vals), "brier": sum(vals) / len(vals)}
        for name, vals in sorted(sums.items())
        if vals
    }
