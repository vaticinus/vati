"""forecast-stack — a leak-free forecasting harness and model.

Layers:
    forecast_stack.eval     scoring, cutoff probe, leak-gated backtests
    forecast_stack.model    keyless quant forecaster, crowd anchor, LLM forecaster, ensemble
    forecast_stack.harness  sealed ledger, session contract
    forecast_stack.feeds    keyless public-data collectors
    forecast_stack.llm      provider-agnostic LLM client
"""
from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
