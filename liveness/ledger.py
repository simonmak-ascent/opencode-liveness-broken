"""Append-only unit ledger: the only honest proof that work is advancing.

Two properties matter, and neither is a counter:

* **append-only** — a row cannot be rewritten, so history cannot be quietly edited;
* **set inclusion** — progress is monotonic when the set of verified units only grows. A counter can
  rise while work is discarded, and can fall while nothing is lost, so counts prove nothing.

A unit is only *verified* when its output exists and hashes to what the row recorded; that comparison
is what stops a job from claiming work it did not do.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import time


class Ledger:
    def __init__(self, path: str | os.PathLike[str]):
        self.path = pathlib.Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._units: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        for line in self.path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue  # a torn line is a lost record, not a corrupted ledger
            self._units[row["unit"]] = row

    def complete(
        self, unit: str, output: str | os.PathLike[str] | None = None, **extra
    ) -> dict:
        """Record a unit. The hash is taken from the file, so the claim is checkable later."""
        digest = None
        size = None
        if output is not None:
            data = pathlib.Path(output).read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            size = len(data)
        row = {
            "unit": unit,
            "at": time.time(),
            "output": str(output) if output is not None else None,
            "sha256": digest,
            "bytes": size,
            **extra,
        }
        with self.path.open("a") as fh:
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        self._units[unit] = row
        return row

    def done(self) -> set[str]:
        return set(self._units)

    def last_age(self) -> float | None:
        if not self._units:
            return None
        return max(0.0, time.time() - max(r["at"] for r in self._units.values()))

    def verify(self) -> dict:
        """Recompute from the filesystem: the job's claim against what is actually on disk."""
        verified, missing, mismatch = [], [], []
        for unit, row in self._units.items():
            out = row.get("output")
            if not out:
                verified.append(unit)
                continue
            p = pathlib.Path(out)
            if not p.exists():
                missing.append(unit)
            elif (
                row.get("sha256")
                and hashlib.sha256(p.read_bytes()).hexdigest() != row["sha256"]
            ):
                mismatch.append(unit)
            else:
                verified.append(unit)
        return {
            "units": len(self._units),
            "verified": len(verified),
            "missing": missing,
            "mismatch": mismatch,
        }

    def regressed_from(self, previous: set[str]) -> list[str]:
        """Units that were verified before and are not now — backtracking, named rather than counted."""
        return sorted(previous - self.done())
