"""Tool changes counted from a G-code file: only a T line naming another head counts (the start code repeats T0).
python tools/test_tool_changes.py  (no printer: the file is in memory)"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import workshop as w  # noqa: E402

g = b"T0\nG28\nT0\nG1 X1\nT1\nG1 X2\nT1\nT0 ; back\nT3\nM104 T2 S0\n"
r = w.tool_changes("x.gcode", read=io.BytesIO(g))
assert r["total"] == 3, r                              # T0 -> T1 -> T0 -> T3; repeats and M104 T2 are not changes
assert [g[o:o + 2] for o in r["offsets"]] == [b"T1", b"T0", b"T3"], r
assert w.tool_changes("y.gcode", read=io.BytesIO(b"T0\nG1\n"))["total"] == 0
# prime tower grams per head: net relative extrusion inside ;TYPE:Prime tower, by each filament's density and diameter
g = (b"; filament_density: 1.27,1.24\n; filament_diameter: 1.75,1.75\nM83\nT0\n;TYPE:Outer wall\nG1 X1 E50\n"
     b";TYPE:Prime tower\nG1 X2 E100\nG1 E-10\nG1 E10\nT1\nG1 X3 E40\n;TYPE:Inner wall\nG1 X4 E999\n"
     b";TYPE:Prime tower\nG1 X5 E-0.8\n")
r = w.tool_changes("y.gcode", read=io.BytesIO(g))
area = 3.14159265 * 0.875 ** 2
assert r["tower_by_head"] == {"T0": round(100 * area * 1.27 / 1000, 1), "T1": round(39.2 * area * 1.24 / 1000, 1)}, r
assert r["tower_g"] == round(sum(r["tower_by_head"].values()), 1) and r["total"] == 1   # walls never count
assert w.tool_changes("z.gcode", read=io.BytesIO(b"T0\nG1 X1 E5\n"))["tower_g"] == 0     # no tower, no waste
print("tool change count check passed")
