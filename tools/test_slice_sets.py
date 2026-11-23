"""Slice overrides: only the whitelisted plain settings reach OrcaSlicer, with sane values.  python tools/test_slice_sets.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import workshop as w  # noqa: E402

w.PLATE = os.path.join(tempfile.mkdtemp(), "plate.json")
seen = {}
w._slice_into = lambda out, d, parts, heads, material, note, preset, sets, req, m3, orca: (seen.update(sets=list(sets)), (200, {"ok": True}))[1]
w.loaded_heads = lambda: [{"colour": "#080a0d", "material": "pla-snapspeed", "label": "PLA black"}]
d, part = {"id": "t", "name": "t"}, [("p", "a.stl", "#080a0d")]
w.slice_parts(d, part, {"preset": "smooth", "set": ["layer=0.08", "wall-speed=45", "wall-accel=1500", "infill=15",
                                                     "layer=0.1", "wall-speed=900", "post_process=rm -rf /", "machine_start_gcode=G28"]})
got = [x for x in seen["sets"] if x.split("=")[0] in ("layer", "wall-speed", "wall-accel", "infill", "post_process", "machine_start_gcode")]
assert got == ["layer=0.08", "wall-speed=45", "wall-accel=1500", "infill=15"], got   # 0.1 is not a whole Z step; 900 mm/s and raw keys are dropped
print("slice set checks passed")
