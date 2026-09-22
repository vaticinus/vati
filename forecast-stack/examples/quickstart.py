"""Offline synthetic lifecycle; never calls a provider or edits a real ledger."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from forecast_stack.harness import ledger
from forecast_stack.model import llm_forecaster


def main() -> None:
    question = "Will the synthetic demonstration event occur?"
    result = llm_forecaster.forecast(question, provider="mock", n=3)
    print("SYNTHETIC OFFLINE DEMO: this probability is not a real-world forecast.")
    print(f"Probability: {result.probability}; sample spread: {result.spread:.4f}")
    with TemporaryDirectory(prefix="forecast-stack-") as directory:
        path = Path(directory) / "events.jsonl"
        event = ledger.append_forecast_sealed(
            path, question_id="demo", run_id="offline", member_id="baseline",
            probability=result.probability, variant="independent",
            forecast_payload={"question": question, "synthetic": True},
        )
        ledger.append_resolution_recorded(
            path, question_id="demo", run_id="offline", outcome=1,
            resolution_payload={"source": "stipulated synthetic outcome"},
        )
        summary = ledger.verify_event_chain(path)
        score = ledger.score_linked_binary_events(path)
        print(f"Forecast record hash: {event['record_hash']}")
        print(f"Verified chain: {summary}")
        print(f"Linked score: {score}")
    print("Temporary ledger removed. Real timing claims need an independent public witness.")


if __name__ == "__main__":
    main()
