"""Checks lift_after_print() against a fake printer: Z only, after PRINT_END's M84, never for tall parts or a homed
printer, motors off again at the end.  python tools/test_bed_lift.py"""
import io
import json
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import workshop as w  # noqa: E402

w.record = lambda *a, **k: None


def fake(height, homed=""):
    sent = []

    def moonraker(method, path, timeout=None):
        if path.startswith("/server/files/metadata"):
            return io.BytesIO(json.dumps({"result": {"object_height": height}}).encode())
        if path.startswith("/printer/objects/query"):
            return io.BytesIO(json.dumps({"result": {"status": {"print_stats": {"state": "complete"}, "idle_timeout": {"state": "Idle"},
                                                                 "toolhead": {"homed_axes": homed}}}}).encode())
        sent.append(urllib.parse.unquote(path.split("script=", 1)[1]))
        return io.BytesIO(b"{}")
    w.moonraker = moonraker
    return sent


s = fake(30.0)
z = w.lift_after_print("a.gcode", wait=1)
assert z == w.view_height(30.0) and s == ["SET_KINEMATIC_POSITION Z=200", "G90", f"G1 Z{z} F600", "M400", "M84"], s
assert not any(c.startswith(("G28", "G1 X", "G1 Y")) or " X" in c or " Y" in c for c in s), s   # Z only, never homes
s = fake(195.0)
assert w.lift_after_print("tall.gcode", wait=1) is None and s == []   # PRINT_END does not park tall parts at Z200
s = fake(30.0, homed="xyz")
assert w.lift_after_print("a.gcode", wait=1) is None and s == []   # still homed: PRINT_END not done, nothing sent
print("bed lift checks passed")
