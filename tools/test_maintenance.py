"""Maintenance by print hours: tasks count from when Workshop first saw the printer, fall due at their interval, and
Done resets them.  python tools/test_maintenance.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import workshop as w  # noqa: E402

w.MAINT = os.path.join(tempfile.mkdtemp(), "maintenance.json")
by = lambda rows: {r["task"]: r for r in rows}
first = by(w.maintenance(hours=26.7))                     # first look: nothing due, not 26.7 h overdue
assert not any(r["due"] for r in first.values()) and first["plate"]["since_h"] == 0, first
later = by(w.maintenance(hours=26.7 + 16))                # 16 h of printing later: the plate (every 15 h) is due, belts are not
assert later["plate"]["due"] and not later["belts"]["due"], later
done = by(w.maintenance("plate", who="Khalid", hours=42.7))
assert not done["plate"]["due"] and done["plate"]["who"] == "Khalid" and done["wipers"]["since_h"] == 16, done
try:
    w.maintenance("nonsense", hours=50)
    raise AssertionError("an unknown task was accepted")
except ValueError:
    pass
print("maintenance checks passed")
