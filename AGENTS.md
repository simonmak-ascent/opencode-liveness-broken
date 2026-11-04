# AGENTS.md

Instructions for agents working in this repository.

## What this is

`opencode-liveness` is a small, dependency-free supervisor that answers one question per operation:
**is it working, or merely alive?** It samples `/proc` at 1 Hz, classifies each registered operation
into one of six states, and escalates with a stack dump and a resumable kill.

It is published openly (MIT) because the failure it prevents is universal to agent harnesses, not
specific to this estate.

## Commands

```bash
python3 -m pytest tests/test_classify.py -q     # six states, synthetic, deterministic
python3 -m pytest tests/test_inject_live.py -q  # real processes, real /proc
python3 -m liveness.cli --state /run/opencode-liveness.json
python3 -m liveness.daemon --socket /run/opencode-liveness.sock
```

Python 3.11+; standard library only. `py-spy` is optional and used for stack capture when present.

## Rules

- **The six states are the contract.** Do not add a seventh without a test that distinguishes it from
  the other six on synthetic samples.
- **`WAITING-ON-CHILD` must never be collapsed into `DEADLOCK`.** That distinction is why this
  repository exists; a parent waiting on a working child is the normal shape of every pipeline.
- **Progress is set inclusion, never a counter.** A unit counts only when its output exists and hashes
  to the ledger's record.
- **Never make the wrapper itself a hang.** Every call to the daemon is bounded and fails open: a
  missing daemon must cost nothing.
- **The daemon is supervised by systemd**, and it pings only when its sampler loop advances — a
  heartbeat thread that pings while the work is frozen is the classic false negative.

## Release

Merge to `main` publishes. The version served by PyPI and npm is the verification; there is no node
this repository deploys to.
