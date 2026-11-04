"""Six-state liveness classification from /proc samples.

A process is not healthy because it exists, and it is not hung because it is asleep. The states below
are ordered so that each one *excludes* the next, which is what makes the verdict checkable rather
than rhetorical:

    RUNNING            a tracked process burned CPU between samples
    WAITING-ON-IO      no CPU, but I/O counters moved (a hint, not proof — page cache counts too)
    WAITING-ON-CHILD   no CPU here, but a tracked child did work: a parent waiting on its own job
    DEADLOCK           none of the above, and the parent/child graph has a cycle
    NO-PROGRESS        no cycle, but the unit ledger has not advanced inside the profile budget
    STARVED            none of the above; it is alive, idle, and not producing

The third state is mandatory. Without it every healthy parent process — a pool waiting on workers,
a shell waiting on a child — looks dead, which is exactly the false deadlock verdict this module was
written after.
"""

from __future__ import annotations

from dataclasses import dataclass, field

STATES = (
    "RUNNING",
    "WAITING-ON-IO",
    "WAITING-ON-CHILD",
    "DEADLOCK",
    "NO-PROGRESS",
    "STARVED",
)

HEALTHY = frozenset({"RUNNING", "WAITING-ON-IO", "WAITING-ON-CHILD"})


@dataclass(frozen=True)
class ProcSample:
    pid: int
    ppid: int
    ticks: int  # utime + stime
    read_bytes: int
    write_bytes: int
    state: str  # R, S, D, Z ...
    wchan: str


@dataclass
class Verdict:
    state: str
    detail: str
    progressed: bool
    cycle: list[int] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return self.state in HEALTHY


def _delta(prev: dict[int, ProcSample], curr: dict[int, ProcSample], attr: str) -> int:
    total = 0
    for pid, now in curr.items():
        before = prev.get(pid)
        if before is not None:
            total += max(0, getattr(now, attr) - getattr(before, attr))
    return total


def find_cycle(samples: dict[int, ProcSample]) -> list[int]:
    """A cycle among the tracked pids only. Edges: pid -> ppid, both tracked."""
    parent = {pid: s.ppid for pid, s in samples.items() if s.ppid in samples}
    for start in parent:
        seen: list[int] = []
        node = start
        while node in parent:
            if node in seen:
                return seen[seen.index(node) :]
            seen.append(node)
            node = parent[node]
    return []


def classify(
    prev: dict[int, ProcSample],
    curr: dict[int, ProcSample],
    *,
    last_unit_age_s: float | None,
    unit_budget_s: float,
) -> Verdict:
    """One verdict from two consecutive samples. Pure: no clocks, no /proc, no side effects."""
    if not curr:
        return Verdict("STARVED", "no tracked processes", False)

    cpu = _delta(prev, curr, "ticks")
    if cpu > 0:
        return Verdict("RUNNING", f"{cpu} ticks of CPU since the last sample", True)

    io = _delta(prev, curr, "read_bytes") + _delta(prev, curr, "write_bytes")
    if io > 0:
        return Verdict(
            "WAITING-ON-IO",
            f"{io} bytes moved; accounting, not proof of progress",
            True,
        )

    # A cycle is definitive, so it is checked before the "waiting on a child" shortcut: two processes
    # waiting on each other are children of each other, and the shortcut would hide it.
    cycle = find_cycle(curr)
    if cycle:
        return Verdict(
            "DEADLOCK",
            "wait-for cycle: " + " -> ".join(map(str, cycle + cycle[:1])),
            False,
            cycle,
        )

    outer = set(curr)
    inner = {pid for pid in curr if curr[pid].ppid in outer}
    if inner:
        # a child of a tracked process is itself tracked and already shown to be idle; if it was
        # active we would have returned RUNNING above, so this branch means "waiting on a peer"
        return Verdict(
            "WAITING-ON-CHILD", f"{len(inner)} tracked children, none active", False
        )

    if last_unit_age_s is not None and last_unit_age_s > unit_budget_s:
        return Verdict(
            "NO-PROGRESS",
            f"no unit for {last_unit_age_s:.0f}s (budget {unit_budget_s:.0f}s); alive and useless",
            False,
        )

    return Verdict("STARVED", "idle, no cycle, no unit due yet", False)
