"""Checks for maintenance.py that need no printer.  python tools/test_maintenance.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import maintenance as m  # noqa: E402

tasks = {"nozzle": {"what": "clean", "every_hours": 50}, "plate": {"what": "wash", "every_prints": 15}, "off": {"what": "x"}}
assert m.due(tasks, {}, 10, 3) == []                                     # fresh printer: nothing due
assert m.due(tasks, {}, 46, 3) == [("nozzle", "clean", 4, "hours")]      # within the last 10%: due soon
assert m.due(tasks, {"nozzle": {"hours": 40}}, 60, 3) == []               # done at 40 h: next at 90 h
assert m.due(tasks, {}, 70, 20) == [("nozzle", "clean", -20, "hours"), ("plate", "wash", -5, "prints")]   # most overdue first
status = {"extruder": {"switch_count": 300, "last_maintenance_count": 100}, "extruder1": {"switch_count": 5}, "heater_bed": {}}
settings = {"extruder": {"individual_maintenance_threshold": 25100}}
assert m.heads(status, settings) == [("extruder", 200, 25100), ("extruder1", 5, 25100)]
assert m.heads({"extruder": {"temperature": 20}}, {}) == []               # a printer that doesn't count swaps
print("maintenance.py checks passed")
