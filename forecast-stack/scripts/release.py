"""Stage explicit public allowlists without git history, data or publication."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
RULES = [
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\b(?:sk-[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|hf_[A-Za-z0-9]{25,}|AKIA[A-Z0-9]{16})\b"),
    re.compile(r"(?:postgres(?:ql)?|mysql)://[^\s'\"`]+:[^\s'\"`]+@"),
]


def public_files(target: str) -> dict[str, bytes]:
    manifest = json.loads((ROOT / "release-files.json").read_text())
    names = manifest[target]
    if len(names) != len(set(names)):
        raise ValueError("Duplicate release paths")
    result = {}
    for name in names:
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or "\\" in name:
            raise ValueError(f"Unsafe release path: {name}")
        if any(part in {".git", ".venv", "node_modules", "data", "dist", "__pycache__"}
               or (part.startswith(".env") and part != ".env.example") for part in relative.parts):
            raise ValueError(f"Private/generated path: {name}")
        path = ROOT.joinpath(*relative.parts)
        if any(parent.is_symlink() for parent in [path, *path.parents] if parent != ROOT.parent):
            raise ValueError(f"Symlink in release path: {name}")
        if not path.is_file() or not path.resolve().is_relative_to(ROOT):
            raise ValueError(f"Missing or escaped release file: {name}")
        if path.stat().st_size > 2_000_000:
            raise ValueError(f"Unexpected large release file: {name}")
        content = path.read_bytes()
        text = content.decode("utf-8")
        if any(rule.search(text) for rule in RULES):
            raise ValueError(f"Possible credential in {name}; inspect privately")
        result[name] = content
    if target == "space":
        result["README.md"] = result.pop("space/README.md")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="check both target allowlists; write nothing")
    parser.add_argument("--target", choices=["github", "space"], default="github")
    parser.add_argument("--out", type=Path, help="new, empty destination outside this repository")
    args = parser.parse_args()
    if args.check:
        for target in ("github", "space"):
            files = public_files(target)
            print(f"{target}: {len(files)} allowlisted UTF-8 files; credential-shape guard passed")
        return
    if args.out is None:
        parser.error("provide --out or --check")
    out = args.out.resolve()
    if out.exists() or out.is_relative_to(ROOT):
        parser.error("destination must not exist and must be outside the source repository")
    files = public_files(args.target)
    out.mkdir(parents=True)
    try:
        for name, content in files.items():
            destination = out / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        manifest = {
            "target": args.target, "published": False,
            "scope": "Explicit public allowlist; no git history or private runtime data.",
            "limitations": "Credential patterns are not a complete secret audit. Hashes do not prove publication time.",
            "files": [{"path": name, "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}
                      for name, content in sorted(files.items())],
        }
        (out / "RELEASE_MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n")
    except BaseException:
        shutil.rmtree(out)  # Only the new directory created by this invocation.
        raise
    print(json.dumps({"directory": str(out), "files": len(files), "target": args.target, "published": False}))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        print(f"Release blocked: {error}", file=sys.stderr)
        sys.exit(1)
