"""The six states, proved on synthetic samples — no processes, no timing, no flakiness."""

from liveness.classify import ProcSample, classify, find_cycle

BUDGET = 60.0


def s(pid, ppid, ticks, read=0, write=0, state="S", wchan="do_poll"):
    return ProcSample(pid, ppid, ticks, read, write, state, wchan)


def test_running_when_cpu_advances():
    prev = {10: s(10, 1, 100)}
    curr = {10: s(10, 1, 150)}
    v = classify(prev, curr, last_unit_age_s=5, unit_budget_s=BUDGET)
    assert v.state == "RUNNING" and v.healthy


def test_waiting_on_io_when_only_bytes_move():
    prev = {10: s(10, 1, 100, read=0)}
    curr = {10: s(10, 1, 100, read=4096)}
    v = classify(prev, curr, last_unit_age_s=5, unit_budget_s=BUDGET)
    assert v.state == "WAITING-ON-IO"


def test_waiting_on_child_is_not_deadlock():
    """The false positive this module exists to prevent: a parent asleep, its child working."""
    prev = {1: s(1, 0, 10), 2: s(2, 1, 100)}
    curr = {1: s(1, 0, 10), 2: s(2, 1, 130)}
    v = classify(prev, curr, last_unit_age_s=120, unit_budget_s=BUDGET)
    assert v.state == "RUNNING"  # the child burned CPU, so the operation is alive

    # and when nothing burns CPU at all, a tracked child still marks "waiting", not "dead"
    v2 = classify(
        {1: s(1, 0, 10), 2: s(2, 1, 100)},
        {1: s(1, 0, 10), 2: s(2, 1, 100)},
        last_unit_age_s=5,
        unit_budget_s=BUDGET,
    )
    assert v2.state == "WAITING-ON-CHILD"


def test_deadlock_needs_a_cycle():
    a = s(1, 2, 10)
    b = s(2, 1, 10)
    assert find_cycle({1: a, 2: b})
    v = classify({1: a, 2: b}, {1: a, 2: b}, last_unit_age_s=5, unit_budget_s=BUDGET)
    assert v.state == "DEADLOCK" and v.cycle


def test_no_progress_when_alive_and_useless():
    prev = {10: s(10, 1, 100)}
    curr = {10: s(10, 1, 100)}
    v = classify(prev, curr, last_unit_age_s=300, unit_budget_s=BUDGET)
    assert v.state == "NO-PROGRESS" and not v.healthy


def test_starved_when_idle_but_not_due():
    prev = {10: s(10, 1, 100)}
    curr = {10: s(10, 1, 100)}
    v = classify(prev, curr, last_unit_age_s=5, unit_budget_s=BUDGET)
    assert v.state == "STARVED"


def test_vanished_process_is_not_healthy():
    v = classify({10: s(10, 1, 100)}, {}, last_unit_age_s=5, unit_budget_s=BUDGET)
    assert not v.healthy
