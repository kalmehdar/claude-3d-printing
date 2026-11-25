"""Maintenance on Snapmaker's U1 schedule: the plate by prints, the rest by days; counts start when Workshop first sees a
task, fall due at the interval, and Done resets them.  python tools/test_maintenance.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import workshop as w  # noqa: E402

w.MAINT = os.path.join(tempfile.mkdtemp(), "maintenance.json")
by = lambda rows: {r["task"]: r for r in rows}
DAY, t0 = 86400, 1_800_000_000
first = by(w.maintenance(jobs=12, now=t0))                       # first look: nothing due, nothing counted from day one
assert not any(r["due"] for r in first.values()) and first["plate"]["since"] == 0, first
later = by(w.maintenance(jobs=22, now=t0 + 31 * DAY))            # 10 prints and 31 days on
assert later["plate"]["due"] and later["belts"]["due"] and not later["zscrew"]["due"], later
assert later["plate"]["unit"] == "prints" and later["belts"]["since"] == 31, later
done = by(w.maintenance("belts", who="Khalid", jobs=22, now=t0 + 31 * DAY))
assert not done["belts"]["due"] and done["belts"]["who"] == "Khalid" and done["plate"]["due"], done
assert by(w.maintenance(jobs=22, now=t0 + 92 * DAY))["zscrew"]["due"]   # quarterly
try:
    w.maintenance("nonsense", jobs=22)
    raise AssertionError("an unknown task was accepted")
except ValueError:
    pass
print("maintenance checks passed")
