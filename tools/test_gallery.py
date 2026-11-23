"""Finished-part gallery: a photo when the bed lifts to the camera after a print, newest first, tagged with who it was
for.  python tools/test_gallery.py"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import printer  # noqa: E402
import workshop as w  # noqa: E402

w.GALLERY = tempfile.mkdtemp()
printer.snap = lambda path, *a, **k: open(path, "wb").write(b"\xff\xd8jpeg")
printer.push = lambda *a, **k: None
for name in ("bed", "take_off_spools", "log_print", "finish_look", "print_doctor", "record"):
    setattr(w, name, lambda *a, **k: {})
w.moonraker = lambda *a, **k: (_ for _ in ()).throw(OSError("no printer in the test"))

a = w.gallery_snap("prints/innaya cotton holder.gcode", settle=0)
assert a.endswith("-innaya_cotton_holder.jpg") and os.path.isfile(os.path.join(w.GALLERY, a)), a
time.sleep(1.1)
b = w.gallery_snap("stand.gcode", settle=0)
rows = w.gallery()
assert [r["photo"] for r in rows] == [b, a] and rows[0]["for"] == "", rows   # newest first, nobody tagged yet
assert w.gallery(a, "Innaya")[1]["for"] == "Innaya"
try:
    w.gallery("../../etc/passwd.jpg", "x")
    raise AssertionError("tagged a photo that does not exist")
except ValueError:
    pass

shots = []                                   # a completed print whose bed rose to the camera gets its photo
w.gallery_snap, w.lift_after_print = (lambda f, **k: shots.append(f)), (lambda f: 45.0)
w.alert_change("printing", {"state": "complete", "file": "x.gcode", "printing_min": 10, "filament_used_m": 1.0})
for _ in range(50):
    if shots:
        break
    time.sleep(0.1)
assert shots == ["x.gcode"], shots
shots.clear(); w.lift_after_print = lambda f: None   # bed did not rise (tall part, or someone started a print): no photo
w.alert_change("printing", {"state": "complete", "file": "y.gcode", "printing_min": 10, "filament_used_m": 1.0})
time.sleep(0.5)
assert shots == [], shots
print("gallery checks passed")
