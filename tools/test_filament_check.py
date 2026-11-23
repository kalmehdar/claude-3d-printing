"""Enough filament: a spool with less left than the file needs is flagged, and Claude's start refuses until the person
says to go ahead.  python tools/test_filament_check.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import printer_mcp as pm  # noqa: E402
import workshop as w  # noqa: E402

data = {"T0": {"spool": "PETG white", "start_g": 1000, "used_g": 100},   # 900 g left
        "T1": {"spool": "PETG black", "start_g": 400, "used_g": 320}}    # 80 g left
meta = {"filament_weight": [50.0, 120.0, 30.0]}                         # T2 has no spool recorded: not judged
short = w.filament_short(meta, data)
assert short == ["T1 PETG black needs 120 g, about 80 g left"], short
assert w.filament_short({"filament_weight": [50.0, 75.0]}, data) == ["T1 PETG black needs 75 g, about 80 g left"]   # within the spare 10 g
assert w.filament_short({"filament_weight": [50.0, 60.0]}, data) == []

calls = []
pm.http = lambda m, path, *a, **k: calls.append((m, path)) or ({"short": short} if "filament-check" in path else {})
pm.mr = lambda m, path, *a, **k: calls.append((m, path)) or {}
try:
    pm.t_start({"filename": "x.gcode", "bed_clear": True})
    raise AssertionError("started with a short spool")
except RuntimeError as e:
    assert "T1 PETG black" in str(e) and not any("print/start" in p for _, p in calls), (e, calls)
assert "Started" in pm.t_start({"filename": "x.gcode", "bed_clear": True, "filament_ok": True})
assert any("print/start" in p for _, p in calls)
calls.clear()                                    # the queue start tells Workshop the bed is clear before starting
pm.t_queue({"action": "start", "bed_clear": True})
assert calls.index(("POST", "/api/bed")) < calls.index(("POST", "/server/job_queue/start")), calls
print("filament check passed")
