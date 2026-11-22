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

# view_bed without homing: raise only, from the remembered Z (the bed can only have sunk below it since)
import tempfile  # noqa: E402
w.LAST_Z = os.path.join(tempfile.mkdtemp(), "last-z.json")


def fake_view(homed=""):
    sent = []

    def moonraker(method, path, timeout=None):
        if path.startswith("/printer/objects/query"):
            return io.BytesIO(json.dumps({"result": {"status": {"print_stats": {"state": "standby"}, "toolhead": {"homed_axes": homed, "position": [0, 0, 45.0, 0]}}}}).encode())
        if path.startswith("/server/history"):
            return io.BytesIO(json.dumps({"result": {"jobs": [{"metadata": {"object_height": 25.0}}]}}).encode())
        sent.append(urllib.parse.unquote(path.split("script=", 1)[1]))
        return io.BytesIO(b"{}")
    w.moonraker = moonraker
    return sent


w.remember_z(200)
s = fake_view()
v = w.view_bed()
assert v == {"z": 45.0, "homed": False} and s == ["SET_KINEMATIC_POSITION Z=200.0", "G90", "G1 Z45.0 F600", "M400", "M84"], (v, s)
assert w.known_z() == 45.0
s = fake_view()   # already at the view height: raising is not possible, so it homes as before (never moves down blind)
assert w.view_bed()["homed"] is True and s[0] == "G28", s
os.remove(w.LAST_Z)
s = fake_view()   # nothing remembered: homes
assert s == [] and w.view_bed()["homed"] is True and s[0] == "G28", s
print("bed lift checks passed")
