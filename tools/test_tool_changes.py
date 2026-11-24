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
print("tool change count check passed")
