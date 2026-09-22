"""A module-style collector must run through the installed CLI and respect its data root."""
from datetime import datetime, timezone
import io
import json
import urllib.request
import zipfile

from forecast_stack import cli, config


def test_collector_cli_writes_normalized_rows_to_configured_directory(tmp_path, monkeypatch):
    today = datetime.now(timezone.utc).date().isoformat()
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("eurofxref.csv", f"Date,USD,GBP,JPY\n{today},1.08,N/A,NaN\ninvalid,2,3,4\n")
    payload = archive.getvalue()
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: io.BytesIO(payload))
    assert cli.main(["feeds", "run", "ecb_fx"]) == 0
    rows = [json.loads(line) for line in (tmp_path / "feeds/ecb_fx.jsonl").read_text().splitlines()]
    assert [(row["date"], row["value"], row["unit"]) for row in rows] == [(today, 1.08, "USD per EUR")]
    assert not (tmp_path / "feeds/ecb_fx.jsonl.tmp").exists()
