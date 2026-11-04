"""The supervisor: a 1 Hz sampler plus a unix-socket protocol.

Design constraints that came out of research, not preference:

* the **work** reports progress; a sidecar thread that pings on its own proves nothing (that is how
  `WatchdogSec` fails silently), so `progress` messages are the only input to NO-PROGRESS;
* classification is per **thread and child**, because a sleeping parent with a working child is the
  normal shape of every pipeline;
* escalation is warn -> dump -> kill, and kill is only acceptable because the caller keeps a ledger
  to resume from;
* this daemon is itself supervised by systemd (`WatchdogSec`), and `jobstat` says so loudly when it is
  absent.

    python -m liveness.daemon --socket /run/opencode-liveness.sock --state /run/opencode-liveness.json
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import signal
import socketserver
import subprocess
import threading
import time

from . import classify as C
from . import sampler

PROFILES = {
    "foreground": {"warn": 30.0, "dump": 90.0, "kill": 180.0, "unit_budget": 60.0},
    "background": {"warn": 120.0, "dump": 600.0, "kill": 1800.0, "unit_budget": 180.0},
}

LOCK = threading.Lock()
OPS: dict[str, dict] = {}
STATE_PATH = pathlib.Path("/run/opencode-liveness.json")
LAST_PING = time.time()


def _record(op: dict, verdict: C.Verdict, age: float) -> None:
    op["state"] = verdict.state
    op["detail"] = verdict.detail
    op["last_verdict_at"] = time.time()
    op["unit_age_s"] = age
    profile = PROFILES[op.get("profile", "foreground")]
    level = op.get("level", 0)
    want = (
        0
        if verdict.healthy
        else (
            3
            if age > profile["kill"]
            else 2
            if age > profile["dump"]
            else 1
            if age > profile["warn"]
            else 1
        )
    )
    if want > level:
        op["level"] = want
        op.setdefault("events", []).append(
            {"at": time.time(), "level": want, "state": verdict.state}
        )
        if want >= 2:
            op["stack"] = _dump_stack(op)
        if want >= 3:
            _kill(op)


def _dump_stack(op: dict) -> str | None:
    """py-spy does not stop the target; gdb does, and /proc/PID/stack is only the kernel stack."""
    if shutil.which("py-spy") is None:
        return None
    out = []
    for pid in sorted(op.get("pids", []))[:4]:
        try:
            res = subprocess.run(
                ["py-spy", "dump", "--pid", str(pid), "--nonblocking"],
                capture_output=True,
                text=True,
                timeout=20,
            )
            out.append(res.stdout.strip()[:4000])
        except Exception as exc:  # capture must never block detection
            out.append(f"py-spy failed: {exc}")
    return "\n---\n".join(out) or None


def _kill(op: dict) -> None:
    for pid in sorted(op.get("pids", [])):
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.kill(pid, sig)
                time.sleep(0.2 if sig == signal.SIGTERM else 0)
            except ProcessLookupError:
                pass
    op["killed"] = True


def _loop(interval: float) -> None:
    global LAST_PING
    prev: dict[str, dict[int, C.ProcSample]] = {}
    while True:
        LAST_PING = time.time()
        with LOCK:
            for op_id, op in OPS.items():
                if op.get("done") or op.get("killed"):
                    continue
                pids = sampler.process_tree(op.get("pids", []))
                curr = sampler.snapshot(pids)
                age = op.get("unit_age_s")
                if op.get("last_unit_at"):
                    age = max(0.0, time.time() - op["last_unit_at"])
                verdict = C.classify(
                    prev.get(op_id, {}),
                    curr,
                    last_unit_age_s=age,
                    unit_budget_s=PROFILES[op.get("profile", "foreground")][
                        "unit_budget"
                    ],
                )
                prev[op_id] = curr
                _record(op, verdict, age or 0.0)
            STATE_PATH.write_text(
                json.dumps(
                    {
                        "updated": time.time(),
                        "daemon_age_s": 0,
                        "ops": {
                            k: {kk: vv for kk, vv in v.items() if kk != "stack"}
                            for k, v in OPS.items()
                        },
                    },
                    indent=1,
                )
            )
        time.sleep(interval)


class Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        for raw in self.rfile:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            with LOCK:
                cmd = msg.get("op")
                op_id = msg.get("id")
                if cmd == "register":
                    OPS[op_id] = {
                        "id": op_id,
                        "pids": [int(p) for p in msg.get("pids", [])],
                        "profile": msg.get("profile", "foreground"),
                        "registered": time.time(),
                        "last_unit_at": None,
                        "unit": None,
                        "state": "RUNNING",
                        "level": 0,
                    }
                elif cmd == "progress" and op_id in OPS:
                    OPS[op_id]["last_unit_at"] = time.time()
                    OPS[op_id]["unit"] = msg.get("unit")
                    OPS[op_id]["level"] = 0
                    OPS[op_id]["done"] = bool(msg.get("done"))
                elif cmd == "status":
                    pass
                payload = {"ops": OPS}
            self.wfile.write(
                (
                    json.dumps(payload if cmd != "register" else {"id": op_id}) + "\n"
                ).encode()
            )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--socket", default="/run/opencode-liveness.sock")
    ap.add_argument("--state", default="/run/opencode-liveness.json")
    ap.add_argument("--interval", type=float, default=1.0)
    args = ap.parse_args()
    global STATE_PATH
    STATE_PATH = pathlib.Path(args.state)
    sock = pathlib.Path(args.socket)
    if sock.exists():
        sock.unlink()
    threading.Thread(target=_loop, args=(args.interval,), daemon=True).start()
    server = socketserver.ThreadingUnixStreamServer(str(sock), Handler)
    os.chmod(sock, 0o660)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
