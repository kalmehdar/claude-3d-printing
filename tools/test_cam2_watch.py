"""Smarter watching with the top view: the plate check before a start (extra evidence only), the prompts naming the top
view and a fallen prime tower, the first-layer plan always the last picture. python tools/test_cam2_watch.py  (stand-ins)"""
import io
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import printer  # noqa: E402
import workshop as w  # noqa: E402

w.record = lambda *a, **k: None
w.wake_camera = lambda: None
printer.camera_url = lambda: "http://printer/cam.jpg"
w.CFG.update(moonraker="http://printer", camera="")


class R(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


w.urllib.request.urlopen = lambda *a, **k: R(b"\xff\xd8low\xff\xd9")
asked = []
verdict = {}
def ask(content, schema, effort="low", max_tokens=4000):
    asked.append(content)
    if verdict.get("raise"):
        raise OSError("API down")
    return dict(verdict)
w.ask_claude = ask
w.api_keys = lambda: ["k"]
top = [b"\xff\xd8top\xff\xd9"]
w.cam2_frame = lambda *a, **k: top[0]

verdict.update(verdict="not_empty", confidence=0.93, reason="a black part at the front left")
why = w.plate_check()
assert why and "front left" in why and "plate checked" in why
c = asked[0]
assert [x["type"] for x in c] == ["image", "image", "text"] and "looking down" in c[2]["text"]   # both views, then the question
for v, conf in (("not_empty", 0.6), ("unsure", 0.99), ("empty", 0.95)):   # not sure enough, unsure, empty: the start goes on
    verdict.update(verdict=v, confidence=conf)
    assert w.plate_check() is None, (v, conf)
verdict["raise"] = True
assert w.plate_check() is None                                   # an API failure never blocks: the person's word stands
verdict.pop("raise")
top[0] = None
asked.clear()
assert w.plate_check() is None and not asked                     # no top view: no extra check, no call

# the guard: refused with the reason, unless the person looked again (X-Plate-Checked)
top[0] = b"\xff\xd8top\xff\xd9"
verdict.update(verdict="not_empty", confidence=0.95, reason="a scrap of filament")
w.bed = lambda *a, **k: {"state": "clear"}
printer.heads_not_loaded = lambda f: []
H = next(v for v in vars(w).values() if isinstance(v, type) and hasattr(v, "guard"))
def guard(headers):
    h = H.__new__(H)
    h.command, h.headers = "POST", headers
    h.body = lambda: b""
    h.printer_busy = lambda: False
    return h.guard("/printer/print/start?filename=a.gcode")
w.moonraker = lambda *a, **k: R(b'{"result": {"queue_state": "paused", "queued_jobs": []}}')
assert "scrap of filament" in (guard({}) or "")
assert guard({"X-Plate-Checked": "1"}) is None

# prompts: the top view and a fallen tower in the watch; the plan is the last picture in the first-layer check
assert "tower_fallen" in w.PAUSE_ON and "top-view picture" in w.PHOTO_PROMPT and "prime tower" in w.PHOTO_PROMPT
assert "tower_fallen" in w.PHOTO_SCHEMA["properties"]["problem"]["enum"]
assert w.FIRST_LAYER_TEXT.lstrip().startswith("The last picture") and "top-view picture" in w.FIRST_LAYER_TEXT
print("top view watching check passed")
