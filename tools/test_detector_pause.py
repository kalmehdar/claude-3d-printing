"""The printer's own spaghetti detector pause: Workshop says why, and Continue sends RESUME only once the printer will
take it (24 Nov 2026: two resumes refused with 'Cannot resume while not paused').  python tools/test_detector_pause.py
No printer is used: Moonraker is mocked."""
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import printer  # noqa: E402
import workshop as w  # noqa: E402

NOODLE = {"id": 532, "index": 0, "code": 2, "level": 2, "message": "detected noodle"}


def st(state, exc=None, paused=True, action=0):
    return {"print_stats": {"state": state, "exception": exc or {}, "message": ""},
            "pause_resume": {"is_paused": paused}, "machine_state_manager": {"main_state": 1, "action_code": action}}


# the reason comes from print_stats.exception
h = printer.hold(st("paused", NOODLE))
assert "spaghetti" in h["why"] and h["can_resume"], h
# the detector fired but print_stats has not caught up: no RESUME yet
h = printer.hold(st("printing", NOODLE, paused=True))
assert not h["can_resume"] and "not finished pausing" in h["waiting"], h
# paused but the machine is still busy (action code not idle)
h = printer.hold(st("paused", NOODLE, action=258))
assert not h["can_resume"] and "busy" in h["waiting"], h
# an ordinary pause by a person: no reason, resumable
h = printer.hold(st("paused"))
assert h["can_resume"] and h["why"] == "", h
assert not printer.hold(st("printing", paused=False))["can_resume"]

# control(): RESUME is sent only when allowed
sent, state = [], {}


def moonraker(method, path, body=None, headers=None, timeout=30):
    sent.append(path)
    if "objects/query" in path:
        return io.BytesIO(json.dumps({"result": {"status": state["s"]}}).encode())
    return io.BytesIO(b'{"result": "ok"}')


w.moonraker = moonraker
state["s"] = st("printing", NOODLE)
code, out = w.control({"action": "resume"})
assert code == 409 and "spaghetti" in out["why"] and not any("RESUME" in p for p in sent), (code, out, sent)
state["s"] = st("paused", NOODLE)
code, out = w.control({"action": "resume"})   # a detector pause needs a person's word
assert code == 409 and "person" in out["error"] and not any("RESUME" in p for p in sent), (code, out)
code, out = w.control({"action": "resume", "person_checked": "true"})   # only a real true
assert code == 409, (code, out)
code, out = w.control({"action": "resume", "person_checked": True})
assert code == 200 and any(p.endswith("script=RESUME") for p in sent), (code, out, sent)
sent.clear()
state["s"] = st("paused")   # a pause by a person (no detector): no extra check
assert w.control({"action": "resume"})[0] == 200 and any(p.endswith("script=RESUME") for p in sent)
assert not w.proxy_allowed("POST", "/printer/print/resume")   # the raw path cannot skip these checks

# detector settings: checked, then one DEFECT_DETECTION_CONFIG
assert w.control({"action": "detector"})[0] == 400
assert w.control({"action": "detector", "sensitivity": "max"})[0] == 400
assert w.control({"action": "detector", "spaghetti": "false"})[0] == 400   # a string is not a switch
sent.clear()
code, out = w.control({"action": "detector", "spaghetti": False})
assert code == 200 and sent[-1].endswith("DEFECT_DETECTION_CONFIG%20NOODLE_ENABLE%3D0"), sent
print("detector pause check passed")
