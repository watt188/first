import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Iterable

VERSION = "6.9"
GENESIS = "0" * 64


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_json(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _safe_relative(path: str) -> str:
    p = Path(path)
    if p.is_absolute() or ".." in p.parts:
        raise ValueError("unsafe_evidence_path")
    normalized = p.as_posix()
    if normalized in ("", "."):
        raise ValueError("unsafe_evidence_path")
    return normalized


def build_manifest(root: str | Path, files: Iterable[str], source_sha: str) -> dict:
    if not source_sha or len(source_sha) < 7:
        raise ValueError("missing_source_sha")
    base = Path(root).resolve()
    previous = GENESIS
    entries = []
    for raw in sorted({_safe_relative(item) for item in files}):
        target = (base / raw).resolve()
        if base != target and base not in target.parents:
            raise ValueError("evidence_path_escape")
        if not target.is_file():
            raise FileNotFoundError(raw)
        payload = target.read_bytes()
        core = {
            "path": raw,
            "sha256": _sha256_bytes(payload),
            "bytes": len(payload),
            "previous_entry_hash": previous,
        }
        entry_hash = _sha256_bytes(_canonical_json(core))
        entry = {**core, "entry_hash": entry_hash}
        entries.append(entry)
        previous = entry_hash
    manifest_core = {
        "version": VERSION,
        "source_sha": source_sha,
        "entries": entries,
        "chain_head": previous,
    }
    return {**manifest_core, "manifest_sha256": _sha256_bytes(_canonical_json(manifest_core))}


def verify_manifest(root: str | Path, manifest: dict, expected_source_sha: str | None = None) -> dict:
    if manifest.get("version") != VERSION:
        raise ValueError("unsupported_manifest_version")
    source_sha = manifest.get("source_sha") or ""
    if expected_source_sha is not None and source_sha != expected_source_sha:
        raise ValueError("source_sha_mismatch")
    base = Path(root).resolve()
    previous = GENESIS
    entries = manifest.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("empty_manifest")
    for entry in entries:
        path = _safe_relative(entry.get("path", ""))
        target = (base / path).resolve()
        if base != target and base not in target.parents:
            raise ValueError("evidence_path_escape")
        if not target.is_file():
            raise ValueError("evidence_file_missing")
        payload = target.read_bytes()
        if entry.get("sha256") != _sha256_bytes(payload):
            raise ValueError("evidence_digest_mismatch")
        if entry.get("bytes") != len(payload):
            raise ValueError("evidence_size_mismatch")
        if entry.get("previous_entry_hash") != previous:
            raise ValueError("evidence_chain_broken")
        core = {
            "path": path,
            "sha256": entry["sha256"],
            "bytes": entry["bytes"],
            "previous_entry_hash": previous,
        }
        computed = _sha256_bytes(_canonical_json(core))
        if entry.get("entry_hash") != computed:
            raise ValueError("evidence_entry_hash_mismatch")
        previous = computed
    if manifest.get("chain_head") != previous:
        raise ValueError("evidence_chain_head_mismatch")
    manifest_core = {
        "version": VERSION,
        "source_sha": source_sha,
        "entries": entries,
        "chain_head": previous,
    }
    if manifest.get("manifest_sha256") != _sha256_bytes(_canonical_json(manifest_core)):
        raise ValueError("manifest_digest_mismatch")
    return {
        "status": "PASSED",
        "version": VERSION,
        "source_sha": source_sha,
        "entry_count": len(entries),
        "chain_head": previous,
        "manifest_sha256": manifest["manifest_sha256"],
    }


def seal(root: str | Path, files: Iterable[str], output: str | Path, source_sha: str) -> dict:
    manifest = build_manifest(root, files, source_sha)
    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def _parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("seal", "verify"))
    parser.add_argument("--root", default=".")
    parser.add_argument("--manifest", default="artifacts/v69-evidence-manifest.json")
    parser.add_argument("--source-sha", default=os.getenv("EVIDENCE_SOURCE_SHA", ""))
    parser.add_argument("files", nargs="*")
    parse = getattr(parser, "parse_intermixed_args", None)
    if parse is not None:
        return parse(argv)
    return parser.parse_args(argv)


def main(argv=None) -> None:
    args = _parse_args(argv)
    if args.mode == "seal":
        if not args.files:
            raise SystemExit("no_evidence_files")
        result = seal(args.root, args.files, args.manifest, args.source_sha)
        print(json.dumps({"status": "SEALED", "manifest_sha256": result["manifest_sha256"], "chain_head": result["chain_head"]}))
    else:
        manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
        print(json.dumps(verify_manifest(args.root, manifest, args.source_sha or None)))


if __name__ == "__main__":
    main()
