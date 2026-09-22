"""The sealed ledger: append, chain, verify, and detect tampering."""
from __future__ import annotations

import json

import pytest

from forecast_stack.harness import ledger


def _seal(path, question_id="q1", probability=0.4):
    return ledger.append_forecast_sealed(
        path, question_id=question_id, run_id="run", member_id="me", probability=probability
    )


def test_seal_and_verify(tmp_path):
    path = tmp_path / "ledger.jsonl"
    event = _seal(path)
    assert event["event_type"] == "forecast_sealed"
    assert event["probability"] == 0.4
    summary = ledger.verify_event_chain(path)
    assert summary["valid"] is True
    assert summary["n"] == 1
    assert summary["head_record_hash"] == event["record_hash"]


def test_events_link_into_a_chain(tmp_path):
    path = tmp_path / "ledger.jsonl"
    first = _seal(path, "q1", 0.4)
    second = _seal(path, "q2", 0.6)
    assert second["prev_record_hash"] == first["record_hash"]
    assert second["chain_index"] == 1
    assert ledger.verify_event_chain(path)["n"] == 2


def test_repeated_identical_content_still_chains(tmp_path):
    path = tmp_path / "ledger.jsonl"
    first = _seal(path, "q1", 0.4)
    second = _seal(path, "q1", 0.4)
    assert second["prev_record_hash"] == first["record_hash"]
    assert ledger.verify_event_chain(path)["n"] == 2


def test_tampering_breaks_verification(tmp_path):
    path = tmp_path / "ledger.jsonl"
    _seal(path, "q1", 0.4)
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    rows[0]["probability"] = 0.99
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    with pytest.raises(ledger.LedgerIntegrityError):
        ledger.verify_event_chain(path)


def test_deleting_a_link_breaks_verification(tmp_path):
    path = tmp_path / "ledger.jsonl"
    _seal(path, "q1", 0.4)
    _seal(path, "q2", 0.6)
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    path.write_text(json.dumps(rows[1]) + "\n")
    with pytest.raises(ledger.LedgerIntegrityError):
        ledger.verify_event_chain(path)


def test_out_of_range_probability_rejected(tmp_path):
    path = tmp_path / "ledger.jsonl"
    with pytest.raises(Exception):
        _seal(path, "q1", 1.5)


def test_verifying_a_missing_ledger_raises(tmp_path):
    with pytest.raises(Exception):
        ledger.verify_event_chain(tmp_path / "does-not-exist.jsonl")


def test_ledger_is_append_only_jsonl(tmp_path):
    path = tmp_path / "ledger.jsonl"
    _seal(path, "q1", 0.4)
    _seal(path, "q2", 0.6)
    lines = [line for line in path.read_text().splitlines() if line.strip()]
    assert len(lines) == 2
    assert json.loads(lines[0])["question_id"] == "q1"
