"""A head's spool -> the slicer filament profile (26 Nov 2026: "PLA Basic" went to pla-snapspeed by colour).
python tools/test_head_material.py  (no printer: Moonraker and the profiles are stand-ins)"""
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orca  # noqa: E402
import workshop as w  # noqa: E402

w.CFG.update(profile="u1")
t = {"filament_color_rgba": ["E2DEDBFF", "080A0DFF", "1C1C1CFF"], "filament_type": ["PLA", "PLA", "PETG"],
     "filament_sub_type": ["Basic", "SnapSpeed", "Generic"], "filament_exist": [True, True, True]}
w.moonraker = lambda *a, **k: io.BytesIO(json.dumps({"result": {"status": {"print_task_config": t}}}).encode())
orca.materials = lambda d: ["pla-snapspeed", "petg-hf", "pla", "petg"]   # unsorted, as os.listdir returns them on Linux
orca.printer_dir = lambda p: "/nonexistent"
got = [h["material"] for h in w.loaded_heads()]
assert got == ["pla", "pla-snapspeed", "petg"], got
print("head material checks passed")
