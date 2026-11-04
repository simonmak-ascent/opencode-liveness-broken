"""Operator surface. Every line answers "is it working", never "does it exist"."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

STATE = pathlib.Path("/run/opencode-liveness.json")


def human(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 5400:
        return f"{seconds / 60:.0f}m"
    return f"{seconds / 3600:.1f}h"


def main() -> int:
    ap = argparse.ArgumentParser(prog="jobstat")
    ap.add_argument("--state", default=str(STATE))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    path = pathlib.Path(args.state)
    if not path.exists():
        print("WATCHDOG DOWN  no state file; nothing is being watched", file=sys.stderr)
        return 2
    state = json.loads(path.read_text())
    age = time.time() - state.get("updated", 0)
    if age > 10:
        print(f"WATCHDOG STALE  last sample {human(age)} ago", file=sys.stderr)

    if args.json:
        print(json.dumps(state, indent=1))
        return 0

    print(f"  {'OP':<22}{'STATE':<18}{'UNIT':<16}{'AGE':>7}{'LVL':>5}")
    for op_id, op in sorted(state.get("ops", {}).items()):
        print(
            f"  {op_id:<22}{op.get('state', '?'):<18}{str(op.get('unit') or '-'):<16}"
            f"{human(op.get('unit_age_s')):>7}{op.get('level', 0):>5}"
        )
    unhealthy = [
        o
        for o in state.get("ops", {}).values()
        if o.get("state") not in ("RUNNING", "WAITING-ON-IO", "WAITING-ON-CHILD")
    ]
    return 1 if unhealthy else 0


if __name__ == "__main__":
    raise SystemExit(main())
