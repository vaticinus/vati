"""Typed contracts for the decomposed forecasting system.

Every division (engine/forecast_system/divisions/*.py) takes/returns these
dataclasses. Nothing here does I/O, LLM, or math — it is the shared vocabulary
the 7 leaf divisions + the mechanical aggregate spine agree on.

Design invariants (see CLAUDE.md doctrine + system prompt):
  * The current-session path preserves one actual reasoner's judgment. The
    optional panel path aggregates method views with declared dependence groups;
    different method names do not establish independent information.
  * Everything carries an ``asof`` boundary. Publication gates constrain admitted
    evidence; they cannot establish the model's knowledge cutoff or make a
    historical judgment evaluation leakage-free.
  * Outside view (ReferenceClass) is produced BEFORE any narrative Evidence is
    retrieved — the base rate must not be contaminated by the story.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field, fields, is_dataclass
from typing import Any

from .ledger import _jsonable as _ledger_jsonable


class ContractError(ValueError):
    """Fail-closed error for malformed prospective forecast contracts."""


def _canonical_obj(obj: Any, *, exclude_fields: set[str]) -> Any:
    """Return the deterministic, JSON-ready payload used for schema hashes.

    The hash helpers intentionally use the same JSON conventions as the existing
    forecasting ledger (sorted keys, compact separators, non-finite floats -> null)
    while letting each dataclass exclude only its own sealing hash field.
    """
    if is_dataclass(obj):
        return {
            f.name: _canonical_obj(getattr(obj, f.name), exclude_fields=exclude_fields)
            for f in fields(obj)
            if f.name not in exclude_fields
        }
    if isinstance(obj, dict):
        return {
            str(k): _canonical_obj(v, exclude_fields=exclude_fields)
            for k, v in obj.items()
            if str(k) not in exclude_fields
        }
    if isinstance(obj, (list, tuple)):
        return [_canonical_obj(v, exclude_fields=exclude_fields) for v in obj]
    return obj


def canonical_payload(obj: Any, *, exclude_fields: set[str] | None = None) -> Any:
    """Canonical JSON-compatible payload for hashing or deterministic logs."""
    return _ledger_jsonable(_canonical_obj(obj, exclude_fields=set(exclude_fields or ())))


def canonical_json(obj: Any, *, exclude_fields: set[str] | None = None) -> str:
    """Deterministic JSON serialization with sorted keys and compact separators."""
    return json.dumps(
        canonical_payload(obj, exclude_fields=exclude_fields),
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_hash(obj: Any, *, exclude_fields: set[str] | None = None) -> str:
    """SHA-256 over :func:`canonical_json`; callers exclude only their seal field."""
    return hashlib.sha256(canonical_json(obj, exclude_fields=exclude_fields).encode("utf-8")).hexdigest()


def _require_text(value: Any, field_name: str, *, exc: type[Exception] = ValueError) -> str:
    if not isinstance(value, str) or not value.strip():
        raise exc(f"{field_name} is required")
    return value.strip()


def _require_str_list(value: Any, field_name: str, *, exc: type[Exception] = ValueError) -> list[str]:
    if not isinstance(value, list):
        raise exc(f"{field_name} must be a list")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise exc(f"{field_name} entries must be non-empty strings")
        out.append(item.strip())
    return out


def _is_hash(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value.lower())


def _require_hash(value: Any, field_name: str, *, exc: type[Exception] = ValueError) -> str:
    if not _is_hash(value):
        raise exc(f"{field_name} must be a 64-character SHA-256 hex digest")
    return str(value).lower()


def _require_hash_list(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    return [_require_hash(item, f"{field_name}[]") for item in value]


def _require_prob(value: Any, field_name: str, *, allow_none: bool = False) -> float | None:
    if value is None and allow_none:
        return None
    try:
        p = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a probability") from exc
    if not math.isfinite(p) or not (0.0 <= p <= 1.0):
        raise ValueError(f"{field_name} must be in [0, 1]")
    return p


def _ensure_current_hash(obj: Any, field_name: str) -> None:
    value = getattr(obj, field_name)
    _require_hash(value, field_name)
    expected = canonical_hash(obj, exclude_fields={field_name})
    if value.lower() != expected:
        raise ValueError(f"{field_name} does not match canonical payload")


# --- division 0: operationalize -------------------------------------------

@dataclass
class Question:
    """A raw prediction target turned into a crisp, resolvable binary claim.

    Produced by the ``operationalize`` division. ``resolution_criteria`` and
    ``kill_criteria`` are fixed here at publish time and copied verbatim onto
    the final Forecast (forecasts are immutable — CLAUDE.md).
    """
    raw: str                                    # the question as received
    operational: str                            # crisp restatement (binary YES/NO)
    resolution_criteria: str                    # what exactly counts as YES
    asof: str                                   # ISO date; knowledge cutoff for this run
    resolution_date: str | None = None          # ISO date the claim resolves
    threshold: float | None = None              # numeric threshold if the claim is "X > t"
    threshold_dir: str = "above"                # "above" | "below"
    kill_criteria: str = ""                      # single falsifier that ends the call
    domain: str = "other"                        # classify_domain-style tag
    notes: str = ""
    exact_outcome: str = ""                      # precise YES outcome fixed before research
    authoritative_resolver: str = ""             # who/what resolves the question
    authoritative_source: str = ""               # source the resolver will rely on, if distinct
    inclusion_rules: list[str] = field(default_factory=list)
    exclusion_rules: list[str] = field(default_factory=list)
    edge_cases: list[str] = field(default_factory=list)
    information_cutoff: str = ""                 # information boundary for the run
    update_cadence: str = ""                     # expected resolution-source update cadence
    scheduled_catalysts: list[str] = field(default_factory=list)
    schema_version: str = "question-contract-v1"
    parse_agreement_status: str = "unverified"   # "agreed" | "judged" | "mock" | "unverified"
    contract_hash: str = ""

    def compute_contract_hash(self) -> str:
        return canonical_hash(self, exclude_fields={"contract_hash"})

    def seal_contract_hash(self) -> "Question":
        self.contract_hash = self.compute_contract_hash()
        return self

    def validate_contract(self, *, require_complete: bool = False, require_hash: bool = False) -> "Question":
        if self.threshold_dir not in {"above", "below"}:
            raise ContractError("threshold_dir must be 'above' or 'below'")
        _require_str_list(self.inclusion_rules, "inclusion_rules", exc=ContractError)
        _require_str_list(self.exclusion_rules, "exclusion_rules", exc=ContractError)
        _require_str_list(self.edge_cases, "edge_cases", exc=ContractError)
        _require_str_list(self.scheduled_catalysts, "scheduled_catalysts", exc=ContractError)
        if require_complete:
            for field_name in (
                "raw",
                "operational",
                "resolution_criteria",
                "asof",
                "resolution_date",
                "exact_outcome",
                "authoritative_resolver",
                "authoritative_source",
                "information_cutoff",
                "update_cadence",
                "kill_criteria",
                "schema_version",
            ):
                _require_text(getattr(self, field_name), field_name, exc=ContractError)
            if self.information_cutoff != self.asof:
                raise ContractError("information_cutoff must match asof")
            if self.parse_agreement_status not in {"agreed", "judged", "mock"}:
                raise ContractError("parse_agreement_status must be sealed")
        if require_hash:
            try:
                _ensure_current_hash(self, "contract_hash")
            except ValueError as exc:
                raise ContractError(str(exc)) from exc
        return self


# --- division 1: reference_class (OUTSIDE VIEW, runs before research) ------

@dataclass
class ReferenceClass:
    """The outside-view base rate for the question, with the class it came from.

    Emitted with NO narrative evidence in context. ``base_rate`` becomes one
    decorrelated column into the aggregate spine.
    """
    description: str                             # the reference class chosen
    base_rate: float                             # P(YES) implied by the class, in [0,1]
    n: int | None = None                         # size of the class, if known
    members: list[str] = field(default_factory=list)   # illustrative prior cases
    reasoning: str = ""


# --- division 2: decompose -------------------------------------------------

@dataclass
class SubForecast:
    """One factor the question factors into, with its own probability + weight.

    The aggregate spine combines these (logit-space weighted mean) into a single
    ``ds_decompose`` column; it does NOT treat each sub as its own column.
    """
    key: str                                     # short handle for the factor
    question: str                                # the sub-question
    p_yes: float                                 # P(this factor favors YES), [0,1]
    weight: float = 1.0                          # relative importance
    combinator: str = "mean"                     # "mean" | "and" | "or" (hint to aggregate)
    reasoning: str = ""


# --- division 3/4: research + filter --------------------------------------

@dataclass
class Evidence:
    """One retrieved item. ``research`` emits raw items (published<=asof only);
    ``filter`` sets credibility/relevance and ``kept``. Downstream divisions
    only ever see ``kept=True`` items — this is the naive-news-RAG guard.
    """
    title: str
    snippet: str
    source: str                                  # 'arxiv' | 'gdelt' | 'world_state' | ...
    published: str | None = None                 # ISO date; MUST be <= asof
    url: str | None = None
    credibility: float | None = None             # [0,1], set by filter
    relevance: float | None = None               # [0,1], set by filter
    kept: bool = True                            # filter's keep/kill decision
    reasoning: str = ""


# --- canonical prospective runtime records --------------------------------

@dataclass
class ForecastMapNode:
    stable_id: str
    label: str
    node_type: str = "claim"
    depends_on: list[str] = field(default_factory=list)
    evidence_atom_hashes: list[str] = field(default_factory=list)
    forecast_hashes: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    node_hash: str = ""

    def compute_node_hash(self) -> str:
        return canonical_hash(self, exclude_fields={"node_hash"})

    def seal_node_hash(self) -> "ForecastMapNode":
        self.node_hash = self.compute_node_hash()
        return self

    def validate(self, *, known_node_ids: set[str] | None = None, require_hash: bool = False) -> "ForecastMapNode":
        _require_text(self.stable_id, "stable_id")
        _require_text(self.label, "label")
        if self.node_type not in {"question", "claim", "factor", "evidence", "forecast", "aggregate", "resolution"}:
            raise ValueError("node_type is invalid")
        _require_str_list(self.depends_on, "depends_on")
        if known_node_ids is not None:
            missing = [node_id for node_id in self.depends_on if node_id not in known_node_ids]
            if missing:
                raise ValueError(f"depends_on references unknown node id: {missing[0]}")
        _require_hash_list(self.evidence_atom_hashes, "evidence_atom_hashes")
        _require_hash_list(self.forecast_hashes, "forecast_hashes")
        if not isinstance(self.metadata, dict):
            raise ValueError("metadata must be a dict")
        if require_hash:
            _ensure_current_hash(self, "node_hash")
        return self


@dataclass
class ForecastMapEdge:
    source_id: str
    target_id: str
    relation: str

    def validate(self, known_node_ids: set[str]) -> "ForecastMapEdge":
        _require_text(self.source_id, "source_id")
        _require_text(self.target_id, "target_id")
        _require_text(self.relation, "relation")
        if self.source_id not in known_node_ids:
            raise ValueError(f"edge source_id references unknown node id: {self.source_id}")
        if self.target_id not in known_node_ids:
            raise ValueError(f"edge target_id references unknown node id: {self.target_id}")
        return self


@dataclass
class ForecastMap:
    question_hash: str
    nodes: list[ForecastMapNode] = field(default_factory=list)
    edges: list[ForecastMapEdge] = field(default_factory=list)
    map_hash: str = ""

    def compute_map_hash(self) -> str:
        return canonical_hash(self, exclude_fields={"map_hash"})

    def seal_map_hash(self) -> "ForecastMap":
        self.map_hash = self.compute_map_hash()
        return self

    def validate(self, *, require_hash: bool = False) -> "ForecastMap":
        _require_hash(self.question_hash, "question_hash")
        if not isinstance(self.nodes, list) or not self.nodes:
            raise ValueError("nodes must be a non-empty list")
        if not isinstance(self.edges, list):
            raise ValueError("edges must be a list")
        node_ids = [node.stable_id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("ForecastMap node stable_id values must be unique")
        known = set(node_ids)
        for node in self.nodes:
            node.validate(known_node_ids=known)
        for edge in self.edges:
            edge.validate(known)
        if require_hash:
            _ensure_current_hash(self, "map_hash")
        return self


@dataclass
class VerifiedEvidenceAtom:
    atom_id: str
    question_hash: str
    claim: str
    source: str
    published_at: str
    captured_at: str
    content_hash: str
    provenance: dict[str, Any]
    direction: str
    depends_on_atom_hashes: list[str] = field(default_factory=list)
    contradicts_atom_hashes: list[str] = field(default_factory=list)
    atom_hash: str = ""

    def compute_atom_hash(self) -> str:
        return canonical_hash(self, exclude_fields={"atom_hash"})

    def seal_atom_hash(self) -> "VerifiedEvidenceAtom":
        self.atom_hash = self.compute_atom_hash()
        return self

    def validate(self, *, require_hash: bool = False) -> "VerifiedEvidenceAtom":
        _require_text(self.atom_id, "atom_id")
        _require_hash(self.question_hash, "question_hash")
        _require_text(self.claim, "claim")
        _require_text(self.source, "source")
        _require_text(self.published_at, "published_at")
        _require_text(self.captured_at, "captured_at")
        _require_hash(self.content_hash, "content_hash")
        if not isinstance(self.provenance, dict) or not self.provenance:
            raise ValueError("provenance must be a non-empty dict")
        if self.direction not in {"supports", "opposes", "neutral", "mixed"}:
            raise ValueError("direction is invalid")
        _require_hash_list(self.depends_on_atom_hashes, "depends_on_atom_hashes")
        _require_hash_list(self.contradicts_atom_hashes, "contradicts_atom_hashes")
        if require_hash:
            _ensure_current_hash(self, "atom_hash")
        return self


@dataclass
class ForecastRecord:
    forecast_id: str
    question_hash: str
    record_type: str                         # "initial" | "revision"
    method_id: str
    prior: float
    p_yes: float | None
    evidence_delta_hashes: list[str]
    dependencies: list[str]
    uncertainty: dict[str, Any]
    triggers: list[str]
    input_hash: str
    response_hash: str
    failure_state: str | None = None
    parent_forecast_hash: str | None = None
    forecast_hash: str = ""

    def compute_forecast_hash(self) -> str:
        return canonical_hash(self, exclude_fields={"forecast_hash"})

    def seal_forecast_hash(self) -> "ForecastRecord":
        self.forecast_hash = self.compute_forecast_hash()
        return self

    def validate(self, *, require_hash: bool = False) -> "ForecastRecord":
        _require_text(self.forecast_id, "forecast_id")
        _require_hash(self.question_hash, "question_hash")
        if self.record_type not in {"initial", "revision"}:
            raise ValueError("record_type must be exactly 'initial' or 'revision'")
        _require_text(self.method_id, "method_id")
        _require_prob(self.prior, "prior")
        if self.failure_state is None:
            _require_prob(self.p_yes, "p_yes")
        elif not isinstance(self.failure_state, str) or not self.failure_state.strip():
            raise ValueError("failure_state must be None or a non-empty string")
        _require_hash_list(self.evidence_delta_hashes, "evidence_delta_hashes")
        _require_hash_list(self.dependencies, "dependencies")
        if not isinstance(self.uncertainty, dict) or not self.uncertainty:
            raise ValueError("uncertainty must be a non-empty dict")
        _require_str_list(self.triggers, "triggers")
        _require_hash(self.input_hash, "input_hash")
        _require_hash(self.response_hash, "response_hash")
        if self.record_type == "initial":
            if self.parent_forecast_hash is not None:
                raise ValueError("initial forecast records cannot have parent_forecast_hash")
        else:
            _require_hash(self.parent_forecast_hash, "parent_forecast_hash")
        if require_hash:
            _ensure_current_hash(self, "forecast_hash")
        return self


@dataclass
class FactReconciliationRecord:
    reconciliation_id: str
    question_hash: str
    input_atom_hashes: list[str]
    reconciled_atom_hash: str
    reconciliation: str
    lineage: list[str]
    reconciliation_hash: str = ""

    def compute_reconciliation_hash(self) -> str:
        return canonical_hash(self, exclude_fields={"reconciliation_hash"})

    def seal_reconciliation_hash(self) -> "FactReconciliationRecord":
        self.reconciliation_hash = self.compute_reconciliation_hash()
        return self

    def validate(self, *, require_hash: bool = False) -> "FactReconciliationRecord":
        _require_text(self.reconciliation_id, "reconciliation_id")
        _require_hash(self.question_hash, "question_hash")
        hashes = _require_hash_list(self.input_atom_hashes, "input_atom_hashes")
        if not hashes:
            raise ValueError("input_atom_hashes must not be empty")
        _require_hash(self.reconciled_atom_hash, "reconciled_atom_hash")
        _require_text(self.reconciliation, "reconciliation")
        _require_str_list(self.lineage, "lineage")
        if require_hash:
            _ensure_current_hash(self, "reconciliation_hash")
        return self


@dataclass
class ForecastReconciliationRecord:
    reconciliation_id: str
    question_hash: str
    input_forecast_hashes: list[str]
    reconciled_forecast_hash: str
    reconciliation: str
    lineage: list[str]
    reconciliation_hash: str = ""

    def compute_reconciliation_hash(self) -> str:
        return canonical_hash(self, exclude_fields={"reconciliation_hash"})

    def seal_reconciliation_hash(self) -> "ForecastReconciliationRecord":
        self.reconciliation_hash = self.compute_reconciliation_hash()
        return self

    def validate(self, *, require_hash: bool = False) -> "ForecastReconciliationRecord":
        _require_text(self.reconciliation_id, "reconciliation_id")
        _require_hash(self.question_hash, "question_hash")
        hashes = _require_hash_list(self.input_forecast_hashes, "input_forecast_hashes")
        if not hashes:
            raise ValueError("input_forecast_hashes must not be empty")
        _require_hash(self.reconciled_forecast_hash, "reconciled_forecast_hash")
        _require_text(self.reconciliation, "reconciliation")
        _require_str_list(self.lineage, "lineage")
        if require_hash:
            _ensure_current_hash(self, "reconciliation_hash")
        return self


@dataclass
class AggregateArtifact:
    artifact_id: str
    question_hash: str
    artifact_role: str                        # "independent" | "crowd_aware"
    independent_forecast_hashes: list[str]
    crowd_forecast_hashes: list[str]
    output_probability: float
    aggregation_method: str
    lineage: list[str]
    artifact_hash: str = ""

    def compute_artifact_hash(self) -> str:
        return canonical_hash(self, exclude_fields={"artifact_hash"})

    def seal_artifact_hash(self) -> "AggregateArtifact":
        self.artifact_hash = self.compute_artifact_hash()
        return self

    def validate(self, *, require_hash: bool = False) -> "AggregateArtifact":
        _require_text(self.artifact_id, "artifact_id")
        _require_hash(self.question_hash, "question_hash")
        if self.artifact_role not in {"independent", "crowd_aware"}:
            raise ValueError("artifact_role must be 'independent' or 'crowd_aware'")
        independent = _require_hash_list(self.independent_forecast_hashes, "independent_forecast_hashes")
        if len(independent) != 5:
            raise ValueError("independent_forecast_hashes must contain exactly five hashes")
        _require_hash_list(self.crowd_forecast_hashes, "crowd_forecast_hashes")
        if self.artifact_role == "independent" and self.crowd_forecast_hashes:
            raise ValueError("independent aggregate cannot include crowd_forecast_hashes")
        _require_prob(self.output_probability, "output_probability")
        _require_text(self.aggregation_method, "aggregation_method")
        _require_str_list(self.lineage, "lineage")
        if require_hash:
            _ensure_current_hash(self, "artifact_hash")
        return self


@dataclass
class RunManifest:
    run_id: str
    question_hash: str
    contract_hash: str
    independent_artifact_hash: str
    crowd_aware_artifact_hash: str | None
    independent_stage_hashes: list[str]
    crowd_stage_hashes: list[str]
    crowd_access_cutoff: str | None = None
    manifest_hash: str = ""

    def compute_manifest_hash(self) -> str:
        return canonical_hash(self, exclude_fields={"manifest_hash"})

    def seal_manifest_hash(self) -> "RunManifest":
        self.manifest_hash = self.compute_manifest_hash()
        return self

    def validate(self, *, require_hash: bool = False) -> "RunManifest":
        _require_text(self.run_id, "run_id")
        _require_hash(self.question_hash, "question_hash")
        _require_hash(self.contract_hash, "contract_hash")
        _require_hash(self.independent_artifact_hash, "independent_artifact_hash")
        if self.crowd_aware_artifact_hash is not None:
            _require_hash(self.crowd_aware_artifact_hash, "crowd_aware_artifact_hash")
            if self.crowd_aware_artifact_hash == self.independent_artifact_hash:
                raise ValueError("independent and crowd-aware artifacts must be separate")
        independent = _require_hash_list(self.independent_stage_hashes, "independent_stage_hashes")
        if not independent:
            raise ValueError("independent_stage_hashes must not be empty")
        _require_hash_list(self.crowd_stage_hashes, "crowd_stage_hashes")
        if self.crowd_stage_hashes and not self.crowd_access_cutoff:
            raise ValueError("crowd_access_cutoff is required when crowd_stage_hashes are present")
        if require_hash:
            _ensure_current_hash(self, "manifest_hash")
        return self


# --- division 5/6: panel + adversarial ------------------------------------

@dataclass
class AnalystView:
    """One isolated analyst's probability under a DELIBERATE framing/prior.

    The panel fans out ``k_analysts`` of these with different framings; the
    adversarial division emits one more (the red-team / opposite-side case).
    Each ``p_yes`` becomes its own decorrelated column into the spine.
    """
    analyst_id: str                              # unique column label, e.g. "analyst_3"
    framing: str                                 # the prior/persona/lens used
    p_yes: float                                 # this analyst's probability, [0,1]
    key_factors: list[str] = field(default_factory=list)
    rationale: str = ""


# --- provenance envelope ---------------------------------------------------

@dataclass
class DivisionResult:
    """A uniform provenance record the pipeline builds per division for the
    Forecast trail. ``probabilities`` are the decorrelated columns this division
    contributed to the spine (empty for research/filter, which produce evidence).
    """
    name: str                                    # division name
    probabilities: dict[str, float] = field(default_factory=dict)
    payload_kind: str = ""                       # e.g. "ReferenceClass", "list[Evidence]"
    count: int = 0                               # n items in payload (evidence/subs/views)
    notes: str = ""


# --- final output ----------------------------------------------------------

@dataclass
class Forecast:
    """The system's final calibrated call: P + interval + kill-criteria + full
    provenance. This is the immutable artifact (supersede, never edit)."""
    question: Question
    asof: str
    p_yes: float                                 # final calibrated probability
    ci_low: float                                # dispersion-based lower band
    ci_high: float                               # dispersion-based upper band
    kill_criteria: str
    resolution_date: str | None
    # mechanical spine trace (all in [0,1] unless noted)
    columns: dict[str, float] = field(default_factory=dict)   # decorrelated inputs
    pooled: float | None = None                  # after pool -> sigmoid
    extremized: float | None = None              # after extremize exponent
    calibrated: float | None = None              # after isotonic map
    anchored: float | None = None                # after early-crowd blend (==p_yes)
    early_crowd: float | None = None             # the bar, if supplied
    provenance: list[DivisionResult] = field(default_factory=list)
    spine: dict[str, Any] = field(default_factory=dict)        # extremize_d, anchor_w, map src
