"""The finish alert carries the print's cost: measured grams per head at the register price.  python tools/test_alert_cost.py"""
import io
import json
import os
import sys
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import printer  # noqa: E402
import workshop as w  # noqa: E402

pushed = []
printer.push = lambda *a, **k: pushed.append(a)
meta = {"filament_type": "PLA;PLA", "filament_used_mm": [30000, 10000], "filament_weight": [90.0, 30.0]}


@contextmanager
def fake(method, path, timeout=10):
    yield io.BytesIO(json.dumps({"result": meta}).encode())


w.moonraker, w.filament_prices = fake, lambda p: {"PLA": 20.0}
for name in ("bed", "take_off_spools", "log_print", "lift_after_print", "finish_look", "print_doctor", "record"):
    setattr(w, name, lambda *a, **k: {})

assert abs(w.print_cost(meta, {"T0": 90.0, "T1": 30.0}) - 2.40) < 1e-9   # 120 g of PLA at £20/kg
w.alert_change("printing", {"state": "complete", "file": "x.gcode", "printing_min": 90, "filament_used_m": 40.0})
title, body, level = pushed[-1][:3]
assert title == "Print finished: x.gcode" and "about £2.40" in body, pushed[-1]
w.filament_prices = lambda p: {}   # no price in the register: no cost, never a guess
w.alert_change("printing", {"state": "complete", "file": "x.gcode", "printing_min": 90, "filament_used_m": 40.0})
assert "£" not in pushed[-1][1], pushed[-1]
print("alert cost checks passed")
