# opencode-liveness

Per-second liveness detection for operations an AI agent triggers. It answers one question honestly:

> **is this operation working, or is it merely alive?**

A process that exists is not evidence. A process that is asleep is not evidence. This tool gives every
registered operation one of **six named states**, checks it at 1 Hz against `/proc`, and escalates with
a stack dump and a kill that resumes from a ledger.

```
RUNNING            CPU advanced between samples
WAITING-ON-IO      no CPU, but I/O counters moved (a hint — page cache counts too)
WAITING-ON-CHILD   no CPU here, but a tracked child is active: a parent waiting on its own job
DEADLOCK           none of the above, and the parent/child graph has a cycle
NO-PROGRESS        no cycle, but the unit ledger has not advanced inside its budget
STARVED            alive, idle, producing nothing, not yet due
```

The first three are healthy. The last three are escalated: warn → dump → kill → resume.

## Why `WAITING-ON-CHILD` exists

Every pipeline looks like this:

```
python parent   S  futex_wait      ← "asleep", and completely healthy
  └── worker     S  do_poll        ← waiting on its child
        └── tesseract  R  (working)
```

A detector that treats "asleep with no output for N minutes" as a hang will kill healthy work — which
is exactly what happened during this project's own development: a 12-hour "hang" that turned out to be
two tesseract processes burning 3.8 cores. The state exists so the false positive is named rather than
alarmed.

## Install

```bash
pipx install opencode-liveness          # daemon + jobstat
```

Plugin (OpenCode V2), in `opencode.json`:

```jsonc
{ "plugins": ["opencode-liveness"] }
```

## Use it in a job

```python
from liveness.ledger import Ledger

ledger = Ledger("/var/lib/myjob/ledger.jsonl")
for document in documents:
    if document.id in ledger.done():      # resume, don't repeat
        continue
    out = process(document)
    ledger.complete(document.id, out)     # hash-linked, fsync'd, append-only
```

Register the operation with the daemon so it is sampled:

```json
{"op":"register","id":"extract-49","pids":[1234],"profile":"background"}
{"op":"progress","id":"extract-49","unit":"hkas20","done":11,"total":49}
```

```bash
jobstat
#   OP                    STATE             UNIT             AGE  LVL
#   extract-49            RUNNING           hkas20            3s    0
```

## Proof of progress, not a counter

Progress is **set inclusion**, never a number going up:

```
units verified   = ledger rows whose output exists and hashes to the recorded digest
monotonic        = U_now ⊇ U_previous
backtracking     = U_previous \ U_now    → the units are named, not counted
```

`claims()` recomputes from the filesystem, so a job cannot claim work it did not do, and a vanished
output is a defect rather than a rounding error.

## What it does not do

- **It cannot see inside a tool.** The OpenCode plugin registers the server's process tree; a tool's
  subprocess is sampled through parent/child edges, but two concurrent tools are one operation unless
  they register themselves.
- **It cannot prove deadlock without stacks.** A cycle in the parent/child graph is necessary; the dump
  is what makes it conclusive. `py-spy` is used when present (non-blocking); otherwise
  `/proc/PID/stack` gives the kernel stack only.
- **It is not a sandbox.** Kill is safe only because callers keep a ledger.

## Design notes

- Stack capture uses `py-spy dump --nonblocking`; `gdb` would stop the target.
- The daemon is supervised by systemd `WatchdogSec`, and pings only when its sampler loop advances —
  a heartbeat thread that pings while the work is frozen is the classic false-negative.
- `jobstat` exits non-zero and prints `WATCHDOG DOWN` when the daemon is absent. A watchdog nobody
  watches is worse than none.

## Tests

```bash
python -m pytest tests/test_classify.py -q        # six states, synthetic, deterministic
python -m pytest tests/test_inject_live.py -q     # real processes, real /proc
```

The live suite spawns a spinner, a sleeping parent with a sleeping child, an idle process past its
budget, and a reader blocked forever on a pipe — and asserts the verdict for each.

MIT.
