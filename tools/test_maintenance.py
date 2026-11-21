"""Checks for maintenance.py that need no printer.  python tools/test_maintenance.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import maintenance as m  # noqa: E402

tasks = {"shafts": {"what": "grease", "every_days": 30}, "plate": {"what": "wash", "every_prints": 15}, "off": {"what": "x"}}
start = {"days": 1000, "hours": 0, "prints": 0}   # the first print, day 1000
now = lambda d, p: {"days": d, "hours": 0, "prints": p}
assert m.due(tasks, {}, now(1010, 3), start) == []                                    # 10 days in: nothing due
assert m.due(tasks, {}, now(1028, 3), start) == [("shafts", "grease", 2, "days")]     # within the last 10%: due soon
assert m.due(tasks, {"shafts": {"days": 1020}}, now(1040, 3), start) == []            # done on day 1020: next on 1050
assert m.due(tasks, {}, now(1050, 20), start) == [("shafts", "grease", -20, "days"), ("plate", "wash", -5, "prints")]   # most overdue first
assert set(m.TASKS) >= {"shafts", "carbon", "belts", "leadscrews", "pins", "pogo"}    # Snapmaker's six components
assert all(any(k.startswith("every_") for k in t) for t in m.TASKS.values())
status = {"extruder": {"switch_count": 300, "last_maintenance_count": 100}, "extruder1": {"switch_count": 5}, "heater_bed": {}}
settings = {"extruder": {"individual_maintenance_threshold": 25100}}
assert m.heads(status, settings) == [("extruder", 200, 25100), ("extruder1", 5, 25100)]
assert m.heads({"extruder": {"temperature": 20}}, {}) == []                           # a printer that doesn't count swaps
print("maintenance.py checks passed")
