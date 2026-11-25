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
w.WATCH_PAUSED_FILE = os.path.join(os.path.dirname(w.PHOTO_LOG), "watch-paused.json")
w._WATCH_PAUSED.clear()
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
# the pause is kept on disk: a restart still needs person_checked for an unanswered watch pause
w._WATCH_PAUSED.clear()
w.watch_paused("run-x", ("spaghetti", 300, False))
saved = json.load(open(w.WATCH_PAUSED_FILE))
assert saved == {"run-x": ["spaghetti", 300, False]}, saved
sent.clear()
assert w.control({"action": "resume"})[0] == 409 and not any("RESUME" in x for x in sent)
assert w.control({"action": "resume", "person_checked": True})[0] == 200
assert json.load(open(w.WATCH_PAUSED_FILE))["run-x"][2] is True     # answered
assert w.control({"action": "resume"})[0] == 200                     # a later pause in the run (magnets) resumes plainly
w.watch_paused("run-x", drop=True)
assert json.load(open(w.WATCH_PAUSED_FILE)) == {}
w.watch_paused("run-y", ("spaghetti", 9, False))
w.watch_paused(None, drop=True)                                       # the print ended: everything goes
assert json.load(open(w.WATCH_PAUSED_FILE)) == {} and not w._WATCH_PAUSED
# a damaged or old file never stops Workshop starting; only whole entries are kept
open(w.WATCH_PAUSED_FILE, "w").write('{"a": 5, "b": null, "c": ["spaghetti", 300], "d": ["spaghetti", 1, false]}')
assert w._load_watch_paused(w.WATCH_PAUSED_FILE) == {"d": ("spaghetti", 1, False)}
open(w.WATCH_PAUSED_FILE, "w").write("[1, 2")
assert w._load_watch_paused(w.WATCH_PAUSED_FILE) == {}
# many threads writing at once: no crash, a readable file
import threading  # noqa: E402
ts = [threading.Thread(target=lambda i=i: [w.watch_paused(f"r{i}", ("spaghetti", n, False)) for n in range(30)]) for i in range(8)]
[t.start() for t in ts]; [t.join() for t in ts]
assert len(json.load(open(w.WATCH_PAUSED_FILE))) == 8
print("watch repause: ok")
