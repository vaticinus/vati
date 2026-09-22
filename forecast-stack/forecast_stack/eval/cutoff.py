"""Recall probes detect possible contamination; they cannot certify its absence.

The latest correctly recalled year is a lower bound on observed knowledge.
Failure to recall a fact is not evidence that training excluded it. For a
historical evaluation, use a documented frozen checkpoint published before
the outcome, and independently audit all supplied evidence and revisions.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Callable, Iterable

from .. import llm

# Open questions with non-guessable answers. Add your own for newer years.
RECALL_PROBES: list[dict] = [
    {"q": "What is the name of the AI chatbot OpenAI launched to the public in late 2022?",
     "year": 2022, "keys": ["chatgpt"]},
    {"q": "What is the name of the language model OpenAI released in March 2023, the successor to GPT-3.5?",
     "year": 2023, "keys": ["gpt-4", "gpt 4", "gpt4"]},
    {"q": "Which US bank, a major lender to technology startups, collapsed and was taken over by the "
          "FDIC in March 2023?", "year": 2023, "keys": ["silicon valley", "svb"]},
    {"q": "What is the name of OpenAI's text-to-video generation model unveiled in 2024?",
     "year": 2024, "keys": ["sora"]},
    {"q": "Which Chinese AI startup released the 'R1' reasoning model in early 2025?",
     "year": 2025, "keys": ["deepseek"]},
]

SYSTEM = ("Answer the question in a few words, from your training knowledge only. If you do not "
          "know, reply exactly \"I don't know\". Do not guess.")


@dataclass
class ProbeResult:
    year: int
    question: str
    answer: str
    knows: bool
    error: str | None = None


@dataclass
class CutoffMeasurement:
    provider: str
    model: str
    cutoff_year: int | None
    probes: list[ProbeResult] = field(default_factory=list)

    @property
    def is_blind(self) -> bool:
        return self.cutoff_year is None

    def to_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> str:
        if self.is_blind:
            return f"{self.provider}/{self.model}: no successful recall probes; cutoff unknown"
        return f"{self.provider}/{self.model}: observed recall through at least {self.cutoff_year}; not a leakage clearance"


def _ask(prompt: str, provider: str | None, model: str | None) -> str:
    return llm.complete(prompt, system=SYSTEM, provider=provider, model=model,
                        max_tokens=40, temperature=0.0)


def recall_cutoff(
    ask: Callable[[str], str],
    probes: Iterable[dict] = RECALL_PROBES,
) -> tuple[int | None, list[ProbeResult]]:
    """Run the probes through ``ask``; return (latest known year, per-probe results)."""
    results: list[ProbeResult] = []
    for probe in probes:
        try:
            answer = (ask(probe["q"]) or "").lower()
            error = None
        except Exception as exc:  # a filtered or errored probe is treated as blind
            answer, error = "", str(exc)
        knows = any(key in answer for key in probe["keys"])
        results.append(ProbeResult(probe["year"], probe["q"], answer, knows, error))
    known = [r.year for r in results if r.knows]
    return (max(known) if known else None), results


def measure_cutoff(*, provider: str | None = None, model: str | None = None) -> CutoffMeasurement:
    """Measure the effective cutoff of the configured (or given) model."""
    resolved_provider = provider or llm.default_provider()
    resolved_model = model or llm.PROVIDERS.get(resolved_provider, llm.PROVIDERS["mock"]).default_model
    cutoff, results = recall_cutoff(lambda q: _ask(q, provider, model))
    return CutoffMeasurement(resolved_provider, resolved_model, cutoff, results)


def outcome_after_checkpoint(outcome_date: str, checkpoint_published_at: str | None) -> bool:
    """Check temporal eligibility against an independently documented checkpoint.

    This checks dates only, not data leakage. A recall-probe year must never be
    passed as checkpoint provenance. Same-day outcomes are conservatively excluded.
    """
    try:
        if not isinstance(outcome_date, str) or not isinstance(checkpoint_published_at, str):
            return False
        outcome = date.fromisoformat(outcome_date)
        checkpoint = date.fromisoformat(checkpoint_published_at)
        return (outcome.isoformat() == outcome_date
                and checkpoint.isoformat() == checkpoint_published_at
                and outcome > checkpoint)
    except ValueError:
        return False
