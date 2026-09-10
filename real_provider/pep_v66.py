import hashlib
import json
import os
import tempfile
import time
from pathlib import Path


class UnifiedPEPV66:
    """Role-aware, symlink-safe, atomic side-effect policy enforcement point."""

    WRITERS = {"frontend", "backend", "test"}

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.audit_path = self.root / "artifacts" / "pep-v66-audit.jsonl"

    @staticmethod
    def _sha256_bytes(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def _audit(self, record):
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"ts_ms": int(time.time() * 1000), **record}
        with self.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")

    def _allowed_for_role(self, role, allowed_paths):
        if isinstance(allowed_paths, dict):
            return set(allowed_paths.get(role, ()))
        return set(allowed_paths)

    def _resolve_target(self, path):
        rel = Path(path)
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError("invalid_path")
        current = self.root
        for part in rel.parts[:-1]:
            current = current / part
            if current.exists() and current.is_symlink():
                raise ValueError("symlink_parent")
        target = self.root / rel
        if target.exists() and target.is_symlink():
            raise ValueError("symlink_target")
        resolved_parent = target.parent.resolve()
        try:
            resolved_parent.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("path_escape") from exc
        return target

    def _authorize(self, role, path, allowed_paths):
        if role not in self.WRITERS:
            return "role_not_writer"
        if path not in self._allowed_for_role(role, allowed_paths):
            return "path_not_allowlisted"
        try:
            self._resolve_target(path)
        except ValueError as exc:
            return str(exc)
        return None

    def write_text(self, role, path, content, allowed_paths):
        reason = self._authorize(role, path, allowed_paths)
        if reason:
            result = {"status": "DENIED", "reason": reason}
            self._audit({"action": "write_text", "role": role, "path": path, **result})
            return result

        target = self._resolve_target(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8")
        before = target.read_bytes() if target.exists() else None
        before_sha = self._sha256_bytes(before) if before is not None else None
        fd, temp_name = tempfile.mkstemp(prefix=".pep-v66-", dir=str(target.parent))
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, target)
        except Exception:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
            raise
        result = {
            "status": "WRITTEN",
            "path": path,
            "before_sha256": before_sha,
            "after_sha256": self._sha256_bytes(data),
            "bytes": len(data),
        }
        self._audit({"action": "write_text", "role": role, **result})
        return result

    def transaction_write_texts(self, operations, role_paths):
        snapshots = {}
        authorized = []
        for op in operations:
            role, path = op["role"], op["path"]
            reason = self._authorize(role, path, role_paths)
            if reason:
                result = {"status": "DENIED", "reason": reason, "path": path, "role": role}
                self._audit({"action": "transaction", **result})
                return result
            target = self._resolve_target(path)
            snapshots[path] = target.read_bytes() if target.exists() else None
            authorized.append((op, target))

        written = []
        try:
            for op, _target in authorized:
                result = self.write_text(op["role"], op["path"], op["content"], role_paths)
                if result.get("status") != "WRITTEN":
                    raise RuntimeError("transaction_write_denied")
                written.append(op["path"])
        except Exception as exc:
            for path in reversed(written):
                target = self._resolve_target(path)
                previous = snapshots[path]
                if previous is None:
                    if target.exists():
                        target.unlink()
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(previous)
            result = {"status": "ROLLED_BACK", "reason": type(exc).__name__, "paths": written}
            self._audit({"action": "transaction", **result})
            return result

        manifest = {path: self._sha256_bytes(self._resolve_target(path).read_bytes()) for path in written}
        result = {"status": "COMMITTED", "paths": written, "manifest_sha256": manifest}
        self._audit({"action": "transaction", **result})
        return result
