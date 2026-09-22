"""A single-question LLM forecaster with self-consistency.

    from forecast_stack.model import forecast

    f = forecast("Will the US CPI YoY print above 3.0% in December 2026?",
                 asof="2026-09-01", provider="deepseek")
    f.probability     # 0.34
    f.samples         # [0.30, 0.40, 0.32]  (n=3 independent samples, blended in logit space)

Self-consistency matters: a single sample is noisy, and the spread across
samples is itself information about how confident the model really is.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from .. import llm

SYSTEM = (
    "You are a careful, calibrated probabilistic forecaster. You are given ONE forecasting question "
    "and the information known as of a stated date; use nothing from after that date. Reason briefly "
    "and concretely: (1) anchor on the right reference class / base rate — for a numeric series, its "
    "recent level, trend, seasonality, and volatility; for an event, how often such outcomes occur; "
    "(2) the main forces pushing the probability UP; (3) the main forces pushing it DOWN, and how far "
    "the as-of evidence should move you from the anchor; (4) reconcile into ONE calibrated probability "
    "— avoid 0 and 1, and don't be falsely confident on genuinely uncertain questions. End your answer "
    "with exactly one line: 'Probability: 0.NN'."
)

_PROB = re.compile(r"probability\s*[:=]\s*([01](?:\.\d+)?|\.\d+)", re.I)
_FLOAT = re.compile(r"\d?\.\d+")
CLIP_LO, CLIP_HI = 0.01, 0.99


def parse_probability(text: str) -> float | None:
    """Pull the final probability out of a reasoning trace, or None."""
    if not text:
        return None
    matches = list(_PROB.finditer(text))
    if matches:
        try:
            return min(CLIP_HI, max(CLIP_LO, float(matches[-1].group(1))))
        except ValueError:
            return None
    for token in reversed(_FLOAT.findall(text)):
        value = float(token)
        if 0 <= value <= 1:
            return min(CLIP_HI, max(CLIP_LO, value))
    return None


def _logit(p: float) -> float:
    p = min(CLIP_HI, max(CLIP_LO, p))
    return math.log(p / (1 - p))


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def build_prompt(
    question: str,
    *,
    resolution_criteria: str | None = None,
    context: str | None = None,
    asof: str | None = None,
) -> str:
    parts = [f"Question: {question}"]
    if resolution_criteria:
        parts.append(f"Resolution criteria: {resolution_criteria}")
    if context:
        parts.append(f"Information as of {asof}:\n{context}")
    parts.append(f"Forecast the probability this resolves YES, as of {asof}.")
    return "\n".join(parts)


@dataclass
class Forecast:
    probability: float
    samples: list[float]
    reasoning: str
    provider: str
    model: str
    failed: int = 0

    @property
    def spread(self) -> float:
        """Std-dev across samples — an honest signal of model disagreement."""
        if len(self.samples) < 2:
            return 0.0
        mean = sum(self.samples) / len(self.samples)
        var = sum((s - mean) ** 2 for s in self.samples) / (len(self.samples) - 1)
        return math.sqrt(var)


def forecast(
    question: str,
    *,
    asof: str | None = None,
    resolution_criteria: str | None = None,
    context: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    n: int = 1,
    temperature: float = 0.7,
) -> Forecast:
    """Forecast one question; ``n`` samples are blended in logit space."""
    prompt = build_prompt(question, resolution_criteria=resolution_criteria,
                          context=context, asof=asof)
    resolved_provider = provider or llm.default_provider()
    resolved_model = (model or __import__("os").environ.get("FORECAST_STACK_MODEL")
                      or llm.PROVIDERS.get(resolved_provider, llm.PROVIDERS["mock"]).default_model)
    samples: list[float] = []
    reasonings: list[str] = []
    failed = 0
    for _ in range(max(1, n)):
        try:
            text = llm.complete(prompt, system=SYSTEM, provider=provider, model=model,
                                max_tokens=700, temperature=temperature)
        except llm.LLMError:
            failed += 1
            continue
        value = parse_probability(text)
        if value is None:
            failed += 1
            continue
        samples.append(value)
        reasonings.append(text.strip())
    if not samples:
        raise llm.LLMError("every sample failed to parse a probability")
    blended = _sigmoid(sum(_logit(s) for s in samples) / len(samples))
    return Forecast(round(blended, 4), samples, "\n\n".join(reasonings),
                    resolved_provider, resolved_model, failed)
