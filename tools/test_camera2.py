"""The second camera: one grab at a time, a shared frame for viewers, a hung or failed camera never breaks a check, the
photo check gets the picture labelled, and the page route answers. python tools/test_camera2.py  (no camera, no Claude)"""
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import workshop as w  # noqa: E402

dev = tempfile.NamedTemporaryFile(delete=False).name   # stands in for /dev/video0
w.CFG["camera2"] = dev
runs, live = [], [0]


controls = []


def fake_run(cmd, capture_output=True, timeout=None):
    if cmd[0] == "v4l2-ctl":   # the controls, set before every grab
        controls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, b"", b"")
    runs.append(cmd)
    live[0] += 1
    assert live[0] == 1, "two ffmpeg processes held the camera at once"
    time.sleep(0.2)
    open(cmd[-1], "wb").write(b"\xff\xd8" + b"x" * 2000 + b"\xff\xd9")
    live[0] -= 1
    return subprocess.CompletedProcess(cmd, 0, b"", b"")


subprocess.run = fake_run
ts = [threading.Thread(target=w.cam2_frame) for _ in range(5)]
[t.start() for t in ts]
[t.join() for t in ts]
assert len(runs) == 1 and runs[0][runs[0].index("-i") + 1] == dev, runs    # five viewers at once: one grab, shared
assert runs[0][runs[0].index("-video_size") + 1] == "2560x1440" and runs[0][runs[0].index("-frames:v") + 1] == "1"
assert "select=gte(n\\,60)" in runs[0][runs[0].index("-vf") + 1]          # 2 s of frames skipped for autofocus
assert len(controls) == 1 and "exposure_time_absolute=600" in controls[0][-1] and "white_balance_temperature=5200" in controls[0][-1]
w.cam2_frame(max_age=0)
assert len(runs) == 2                                                       # a fresh one when asked for

# 1440p refused: 1080p
sizes = []
def no_1440(cmd, capture_output=True, timeout=None):
    if cmd[0] == "v4l2-ctl":
        return subprocess.CompletedProcess(cmd, 0, b"", b"")
    size = cmd[cmd.index("-video_size") + 1]
    sizes.append(size)
    if size == "2560x1440":
        return subprocess.CompletedProcess(cmd, 1, b"", b"Invalid argument")
    open(cmd[-1], "wb").write(b"\xff\xd8" + b"y" * 2000 + b"\xff\xd9")
    return subprocess.CompletedProcess(cmd, 0, b"", b"")
subprocess.run = no_1440
assert w.cam2_frame(max_age=0).startswith(b"\xff\xd8y") and sizes == ["2560x1440", "1920x1080"], sizes

# no v4l2-ctl on the box: still a picture, with the camera's own settings
def no_ctl(cmd, capture_output=True, timeout=None):
    if cmd[0] == "v4l2-ctl":
        raise FileNotFoundError("v4l2-ctl")
    open(cmd[-1], "wb").write(b"\xff\xd8" + b"z" * 2000 + b"\xff\xd9")
    return subprocess.CompletedProcess(cmd, 0, b"", b"")
subprocess.run = no_ctl
assert w.cam2_frame(max_age=0).startswith(b"\xff\xd8z")

# a failed or hung camera: the last good frame (under a minute old) or None, never an exception
subprocess.run = lambda *a, **k: subprocess.CompletedProcess(a[0], 1, b"", b"Device or resource busy")
assert w.cam2_frame(max_age=0).startswith(b"\xff\xd8")
def hang(*a, **k):
    raise subprocess.TimeoutExpired(a[0], 20)
subprocess.run = hang
w._cam2["t"] = time.time() - 120
assert w.cam2_frame(max_age=0) is None

# none configured, or switched off
w.CFG["camera2"] = "none"
assert w.cam2_device() is None and w.cam2_frame() is None
w.CFG["camera2"] = "/dev/no-such-camera"
assert w.cam2_device() is None
w.CFG["camera2"] = dev

# the photo check sends it after the main picture, labelled
sent = []
class R(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False
def fake_open(req, timeout=None):
    sent.append(json.loads(req.data))
    return R(json.dumps({"stop_reason": "end_turn", "usage": {}, "content": [{"type": "text", "text": json.dumps(
        {"verdict": "ok", "problem": "none", "confidence": 0.9, "reason": "fine"})}]}).encode())
w.api_keys = lambda: ["k"]
w.urllib.request.urlopen = fake_open
main = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False).name
open(main, "wb").write(b"\xff\xd8main\xff\xd9")
w.look_at_photo(main, 12, front=b"\xff\xd8front\xff\xd9")
content = sent[0]["messages"][0]["content"]
kinds = [c["type"] for c in content]
assert kinds == ["image", "text", "image", "text"] and content[1]["text"] == w.CAM2_LABEL, kinds
w.look_at_photo(main, 12)
assert [c["type"] for c in sent[1]["messages"][0]["content"]] == ["image", "text"]   # no second camera: as before

# the page route
H = next(v for v in vars(w).values() if isinstance(v, type) and hasattr(v, "guard"))
def get(path):
    h = H.__new__(H)
    h.command, h.path, h.headers = "GET", path, {}
    out = []
    h.send = lambda code, body=None, ctype="application/json", *a: out.append((code, ctype))
    h.guard = lambda p: None
    h.allowed = lambda: True
    h.route()
    return out[0]
w._cam2.update(t=time.time(), jpg=b"\xff\xd8ok\xff\xd9")
assert get("/cam2.jpg?t=1") == (200, "image/jpeg")
w.CFG["camera2"] = "none"
assert get("/cam2.jpg") [0] == 404
print("second camera check passed")
