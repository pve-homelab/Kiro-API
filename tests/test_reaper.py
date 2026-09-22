"""Unit tests for the reaper's tracking and deadline logic."""
from __future__ import annotations

from app.reaper import Reaper, ReaperStats


async def test_track_untrack():
    r = Reaper()
    # Use our own PID (always exists) so getpgid works.
    import os
    await r.track(os.getpid(), hard_timeout=100)
    assert len(r._jobs) == 1
    await r.untrack(os.getpid())
    assert len(r._jobs) == 0


async def test_kill_all_jobs_clears_tracking():
    r = Reaper()
    import os
    await r.track(os.getpid(), hard_timeout=100)
    # kill_all_jobs will try to SIGKILL; guard against actually killing the test
    # process by pointing the tracked pgid at a non-existent group.
    for job in r._jobs.values():
        job.pgid = 2_000_000_000  # nonexistent -> ProcessLookupError, safely ignored
    killed = await r.kill_all_jobs()
    assert killed == 1
    assert len(r._jobs) == 0


def test_stats_snapshot_keys():
    s = ReaperStats()
    snap = s.snapshot()
    for key in ("zombies_reaped", "jobs_force_killed", "gc_runs",
                "last_child_count", "peak_child_count", "leak_warning"):
        assert key in snap


async def test_deadline_not_triggered_early():
    r = Reaper()
    import os
    await r.track(os.getpid(), hard_timeout=1000)
    # Sweep should not kill a job whose deadline is far in the future.
    await r._enforce_deadlines()
    assert len(r._jobs) == 1
    await r.untrack(os.getpid())
