"""Release staging must not disclose unlisted files, credentials or symlink targets."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.fixture
def candidate(tmp_path):
    root = tmp_path / "source"
    (root / "scripts").mkdir(parents=True)
    shutil.copyfile(Path(__file__).parents[1] / "scripts/release.py", root / "scripts/release.py")
    (root / "public.txt").write_text("public code\n")
    (root / "release-files.json").write_text(json.dumps({"github": ["public.txt"], "space": []}))
    return root, tmp_path / "export"


def stage(root, out):
    return subprocess.run([sys.executable, str(root / "scripts/release.py"), "--out", str(out)],
                          text=True, capture_output=True)


def test_release_excludes_unlisted_secrets_and_hashes_exported_bytes(candidate):
    root, out = candidate
    (root / ".env").write_text("PRIVATE_VALUE=do-not-publish\n")
    assert stage(root, out).returncode == 0
    assert not (out / ".env").exists()
    manifest = json.loads((out / "RELEASE_MANIFEST.json").read_text())
    assert manifest["files"] == [{"path": "public.txt", "sha256": hashlib.sha256(b"public code\n").hexdigest(), "bytes": 12}]


def test_release_blocks_allowlisted_credential_before_creating_output(candidate):
    root, out = candidate
    (root / "public.txt").write_text("sk-" + "x" * 30)
    assert stage(root, out).returncode != 0
    assert not out.exists()


def test_release_blocks_symlink_and_preserves_existing_destination(candidate):
    root, out = candidate
    (root / "public.txt").unlink()
    private = root.parent / "private.txt"
    private.write_text("private record")
    (root / "public.txt").symlink_to(private)
    assert stage(root, out).returncode != 0
    assert not out.exists()
    out.mkdir()
    (out / "keep.txt").write_text("user work")
    assert stage(root, out).returncode != 0
    assert (out / "keep.txt").read_text() == "user work"
