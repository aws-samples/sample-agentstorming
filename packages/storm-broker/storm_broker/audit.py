# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Append-only audit chain.

Every broker call logged with (event_id, persona_pid, room_id,
task_id, tool, args_hash, decision, duration_ms, result_size_bytes,
timestamp). Entries are cryptographically chained — each entry's
hash includes the previous entry's hash — so tampering is detectable.

For v0 we use a JSON-Lines file and HMAC-SHA-256 chaining. For
production we'd ship a daily Merkle root to Sigstore Rekor for
external attestation.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class AuditEntry:
    event_id: str
    persona_pid: str
    room_id: str
    task_id: str
    tool: str
    args_hash: str
    decision: str  # "allow" | "deny" | "rate_limited" | "approval_required" | ...
    reason: str
    duration_ms: int = 0
    result_size_bytes: int = 0
    timestamp: float = field(default_factory=time.time)
    prev_hash: str = ""
    entry_hash: str = ""


class AuditChain:
    def __init__(self, path: Path, hmac_key: bytes) -> None:
        self._path = path
        self._key = hmac_key
        self._lock = threading.Lock()
        self._prev_hash = self._read_last_hash()

    def _read_last_hash(self) -> str:
        if not self._path.exists() or self._path.stat().st_size == 0:
            return ""
        # Read last line.
        with open(self._path, "rb") as f:
            try:
                f.seek(-2, os.SEEK_END)
                while f.read(1) != b"\n":
                    f.seek(-2, os.SEEK_CUR)
            except OSError:
                f.seek(0)
            last = f.readline().decode("utf-8").strip()
        if not last:
            return ""
        return json.loads(last).get("entry_hash", "")

    def append(self, entry: AuditEntry) -> AuditEntry:
        with self._lock:
            entry.prev_hash = self._prev_hash
            entry.entry_hash = self._compute_hash(entry)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(entry), separators=(",", ":")) + "\n")
            self._prev_hash = entry.entry_hash
        return entry

    def _compute_hash(self, entry: AuditEntry) -> str:
        # Canonical body (without entry_hash itself).
        body = {k: v for k, v in asdict(entry).items() if k != "entry_hash"}
        canonical = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return hmac.new(self._key, canonical, hashlib.sha256).hexdigest()

    def verify(self) -> tuple[bool, int]:
        """Walk the file, verify chain integrity. Returns (ok, line_count)."""
        if not self._path.exists():
            return True, 0
        prev = ""
        n = 0
        with open(self._path, "r", encoding="utf-8") as f:
            for line in f:
                n += 1
                d = json.loads(line)
                if d.get("prev_hash") != prev:
                    return False, n
                expected = self._compute_hash(AuditEntry(**{k: v for k, v in d.items() if k != "entry_hash"}))
                if d.get("entry_hash") != expected:
                    return False, n
                prev = d["entry_hash"]
        return True, n


def args_hash(args: dict[str, Any]) -> str:
    """Stable canonical hash of tool args (no secrets in audit log)."""
    canonical = json.dumps(args, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
