"""Append-only, hash-chained audit log.

Every record links to the one before it:

    record_hash = sha256(prev_hash + canonical_json(record))

so any edit or deletion of a past record breaks the chain from that point on and
``verify_chain`` reports the exact sequence number where it broke.  Records are
only ever appended; a human review decision is a *new* record referencing the
gate decision, never a mutation of it.  That is the property you want when
somebody later asks "who put this in the CRM, and on what evidence".

Log line format (one JSON object per line):

    {"seq": int, "prev_hash": hex, "hash": hex, "record": {...}}
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterator

GENESIS = "0" * 64


def canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _link(prev_hash: str, record: dict) -> str:
    return sha256(prev_hash.encode() + canonical(record)).hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass
class AuditLog:
    path: Path

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # ---- reading -------------------------------------------------------
    def entries(self) -> Iterator[dict]:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield json.loads(line)

    def head(self) -> tuple[int, str]:
        seq, prev = -1, GENESIS
        for entry in self.entries():
            seq, prev = entry["seq"], entry["hash"]
        return seq, prev

    # ---- writing -------------------------------------------------------
    def append(self, event: str, **fields: Any) -> dict:
        seq, prev = self.head()
        record = {"seq": seq + 1, "ts": now_iso(), "event": event, **fields}
        entry = {
            "seq": record["seq"],
            "prev_hash": prev,
            "hash": _link(prev, record),
            "record": record,
        }
        # Append-only: open in append mode, flush and fsync so a crash cannot
        # leave a decision applied but unlogged.
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True, ensure_ascii=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return entry

    # ---- integrity -----------------------------------------------------
    def verify_chain(self) -> tuple[bool, str]:
        prev, expected_seq = GENESIS, 0
        count = 0
        for entry in self.entries():
            rec = entry["record"]
            if entry["seq"] != expected_seq or rec.get("seq") != expected_seq:
                return False, f"sequence gap at line {count + 1}: expected seq {expected_seq}"
            if entry["prev_hash"] != prev:
                return False, f"broken link at seq {entry['seq']}: prev_hash does not match"
            if entry["hash"] != _link(entry["prev_hash"], rec):
                return False, f"record altered at seq {entry['seq']}: hash mismatch"
            prev, expected_seq, count = entry["hash"], expected_seq + 1, count + 1
        return True, f"chain intact: {count} records, head={prev[:16]}"
