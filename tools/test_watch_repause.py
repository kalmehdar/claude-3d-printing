"""The photo watch pauses once for a finding; after a person resumes, the same finding warns once and does not pause again
for REPAUSE_GAP layers. Another problem, or a new run, still pauses.
python tools/test_watch_repause.py  (Claude, the camera and the printer are stand-ins)"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import printer  # noqa: E402
import workshop as w  # noqa: E402

pauses, pushes, problem = [], [], ["spaghetti"]
printer.push = lambda title, body="", *a, **k: pushes.append(title)
printer.snap = lambda path: None
printer.status = lambda: {"state": "printing"}
w.time.sleep = lambda s: None
w.look_at_photo = lambda *a, **k: {"verdict": "pause", "problem": problem[0], "confidence": 0.95, "reason": "Tangle on the tower."}
w.parts = lambda: {"objects": []}
w.record = lambda *a, **k: None
w.print_doctor = lambda *a, **k: None
w.moonraker = lambda method, path, **k: pauses.append(path) or open(os.devnull, "rb")
w.PHOTO_LOG = os.path.join(tempfile.mkdtemp(), "log.jsonl")
run = tempfile.mkdtemp()


def look(layer, folder=run):
    w.photo_watch("hoodie.gcode", layer, os.path.join(folder, f"{layer}.jpg"), first=False)


look(419)
assert len(pauses) == 1, pauses                         # first finding: paused
look(421)
assert len(pauses) == 1 and pushes[-1].startswith("Still seeing spaghetti"), pushes   # resumed, same finding: told once
n = len(pushes)
look(431)
assert len(pauses) == 1 and len(pushes) == n, pushes    # and not told again
problem[0] = "detached"
look(440)
assert len(pauses) == 2                                 # another problem still pauses
problem[0] = "spaghetti"
look(445)
assert len(pauses) == 3                                 # the last pause was for something else: spaghetti pauses again
look(450)
assert len(pauses) == 3
look(495)
assert len(pauses) == 4                                 # past the gap: pauses again
look(10, tempfile.mkdtemp())
assert len(pauses) == 5                                 # a new run starts fresh
print("watch repause: ok")
