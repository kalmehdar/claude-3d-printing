"""Failed-print triage: when the printer pauses itself, the print doctor runs (once) with a fresh camera picture and
pushes the likely cause; a person's pause or the photo watch's own pause do not trigger it.
python tools/test_pause_triage.py  (Claude, the camera, the printer and the phone are stand-ins)"""
import os
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import printer  # noqa: E402
import workshop as w  # noqa: E402

pushes, asked, done = [], [], threading.Event()
printer.push = lambda title, body="", *a, **k: pushes.append((title, body))
printer.fetch = lambda path, tail=0: b"; pressure_advance = 0.04\n; nozzle_temperature = 245\n"
printer.snap = lambda path: open(path, "wb").write(b"\xff\xd8 fresh picture")
w.CFG.update(moonraker="http://printer", camera="", url="")
w.api_key = lambda: "test-key"
w.record = lambda *a, **k: None
w.bed = lambda *a, **k: None
w.last_layer_photo = lambda f: None
w.DOCTOR = tempfile.mkdtemp()
w.PHOTO_LOG = os.path.join(w.DOCTOR, "none.jsonl")


def ask(content, schema, effort="low", max_tokens=4000):
    asked.append(content)
    done.set()
    return {"diagnosis": "Loose strands round the left foot: the part lifted.", "causes": ["adhesion"],
            "changes": [{"setting": "nozzle_temperature", "now": "245", "suggested": "240", "why": "less ooze"}], "next_step": "Cancel it."}


w.ask_claude = ask
base = {"file": "ghost.gcode", "printing_min": 30, "filament_used_m": 2.0}

# the printer's own detector paused it: one doctor run, the fresh picture last, a push with the cause and the change
w.alert_change("printing", {**base, "state": "paused", "message": "Paused by the printer's own detector: spaghetti."})
assert done.wait(5), "the doctor did not run"
images = [c for c in asked[0] if c["type"] == "image"]
text = asked[0][-1]["text"]
assert len(images) == 1 and "printer paused it itself" in text and "safe to continue" in text, text[:200]
assert any(t.startswith("Print paused") for t, _ in pushes)
doc = [b for t, b in pushes if t.startswith("Print doctor")]
assert doc and "lifted" in doc[0] and "nozzle_temperature 245 to 240" in doc[0], pushes

# a person's pause, a pause at a layer, the photo watch's own pause: no message, no doctor
asked.clear()
done.clear()
w.alert_change("printing", {**base, "state": "paused", "message": ""})
w.alert_change("printing", {**base, "state": "paused"})
assert not done.wait(0.5) and not asked
# the U1 may keep its exception after a RESUME: a later pause in the same run (a person's, or the photo watch's) with the
# same message must not run the doctor again; a new run (another photo folder) may
reason = "Paused by the printer's own detector: spaghetti."
w.alert_change("printing", {**base, "state": "paused", "message": reason})   # same file, folder None, message: seen
assert not done.wait(0.5) and not asked
import glob  # noqa: E402
had = set(glob.glob(os.path.join(tempfile.gettempdir(), "doctor-*")))
w.alert_change("printing", {**base, "state": "paused", "message": reason}, folder="ghost-0000AB")   # a new run
assert done.wait(5) and len(asked) == 1
assert set(glob.glob(os.path.join(tempfile.gettempdir(), "doctor-*"))) == had, "this pause left a snapshot folder behind"
# the run ends: its entries go, so a reprint (folder None again before its first layer photo) is looked at
asked.clear()
done.clear()
w.alert_change("paused", {**base, "state": "cancelled"})
assert not any(k[0] == "ghost.gcode" for k in w._doctored), w._doctored
done.wait(5)   # the cancel's own doctor run
asked.clear()
done.clear()
w.alert_change("printing", {**base, "state": "paused", "message": reason})
assert done.wait(5) and len(asked) == 1
# a cancel before anything printed (still heating): no doctor, no push about an empty plate
asked.clear()
done.clear()
pushes.clear()
w.alert_change("printing", {"file": "empty.gcode", "state": "cancelled", "printing_min": 0.0, "filament_used_m": 0.0})
assert not done.wait(0.5) and not asked and not any(t.startswith("Print doctor") for t, _ in pushes), pushes
w.alert_change("printing", {"file": "empty.gcode", "state": "cancelled", "printing_min": 12.0, "filament_used_m": 1.5})
assert done.wait(5) and len(asked) == 1                         # a cancel after printing: the doctor looks, as before
print("pause triage check passed")
