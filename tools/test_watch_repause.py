"""The photo watch pauses once for a finding; after a person resumes, the same finding warns (every time) and does not
pause again for REPAUSE_GAP layers. If the warning reaches nobody, it pauses. Another problem, or a new run, still pauses.
An API resume after a watch pause needs person_checked.
python tools/test_watch_repause.py  (Claude, the camera and the printer are stand-ins)"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import printer  # noqa: E402
import workshop as w  # noqa: E402

pauses, pushes, problem, delivered = [], [], ["spaghetti"], [True]
printer.push = lambda title, body="", *a, **k: pushes.append(title)
printer.notify = lambda title, body="", *a, **k: pushes.append(title) or delivered[0]
printer.snap = lambda path: None
printer.status = lambda: {"state": "printing"}
w.time.sleep = lambda s: None
w.look_at_photo = lambda *a, **k: {"verdict": "pause", "problem": problem[0], "confidence": 0.95, "reason": "Tangle on the tower."}
w.parts = lambda: {"objects": []}
w.record = lambda *a, **k: None
w.print_doctor = lambda *a, **k: None
w.moonraker = lambda method, path, **k: pauses.append(path) or __import__("io").BytesIO(b"{}")
w.PHOTO_LOG = os.path.join(tempfile.mkdtemp(), "log.jsonl")
run = tempfile.mkdtemp()


def look(layer, folder=run):
    w.photo_watch("hoodie.gcode", layer, os.path.join(folder, f"{layer}.jpg"), first=False)


look(419)
assert len(pauses) == 1, pauses                         # first finding: paused
look(421)
assert len(pauses) == 1 and pushes[-1].startswith("Still seeing spaghetti"), pushes   # resumed, same finding: told once
look(431)
assert len(pauses) == 1 and pushes[-1].startswith("Still seeing") and pushes[-2].startswith("Still seeing")   # told every time
delivered[0] = None
look(435)
assert len(pauses) == 2, pauses                         # nobody could be told: it pauses as before
delivered[0] = True
problem[0] = "detached"
look(440)
assert len(pauses) == 3                                 # another problem still pauses
problem[0] = "spaghetti"
look(445)
assert len(pauses) == 4                                 # the last pause was for something else: spaghetti pauses again
look(450)
assert len(pauses) == 4
look(495)
assert len(pauses) == 5                                 # past the gap: pauses again
look(10, tempfile.mkdtemp())
assert len(pauses) == 6                                 # a new run starts fresh
# resume through the API after a watch pause: refused without a person's check, sent with it
import io, json  # noqa: E401,E402
held = {"print_stats": {"state": "paused"}, "pause_resume": {"is_paused": True}, "machine_state_manager": {}}
sent = []
def mr(method, path, **k):
    sent.append(path)
    return io.BytesIO(json.dumps({"result": {"status": held}}).encode())
w.moonraker = mr
printer.hold = lambda s: {"can_resume": True, "why": "", "waiting": ""}
code, body = w.control({"action": "resume"})
assert code == 409 and "photo watch" in body["error"] and not any("RESUME" in x for x in sent), body
code, body = w.control({"action": "resume", "person_checked": True})
assert code == 200 and any("RESUME" in x for x in sent), body
w._WATCH_PAUSED.clear()
code, body = w.control({"action": "resume"})
assert code == 200                                       # no watch pause in this run: a plain resume as before
print("watch repause: ok")
