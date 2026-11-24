"""The top view's live stream: whole JPEGs out of ffmpeg's MJPEG pipe, every viewer fed, stills from its latest frame while it
runs, the camera lock held while live and freed when the last viewer goes. python tools/test_cam2_stream.py  (no camera)"""
import io
import os
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import workshop as w  # noqa: E402

w.CFG["camera2"] = tempfile.NamedTemporaryFile(delete=False).name
w.CAM2_CONF = os.path.join(tempfile.mkdtemp(), "camera2.json")
frames = [b"\xff\xd8" + bytes([i]) * 3000 + b"\xff\xd9" for i in range(1, 30)]


class Pipe:
    """ffmpeg's stdout: the frames back to back, cut at awkward places, then slow, like a camera at 15 fps."""
    def __init__(self):
        self.data, self.i = b"".join(frames), 0
    def read(self, n):
        if self.i >= len(self.data):
            time.sleep(0.05)
            return b"\xff\xd8" + b"\x01" * 100 + b"\xff\xd9" if not stopped.is_set() else b""
        chunk = self.data[self.i:self.i + 1777]
        self.i += 1777
        time.sleep(0.002)
        return chunk


stopped, procs = threading.Event(), []
class Proc:
    def __init__(self, cmd, **k):
        procs.append(cmd)
        self.stdout = Pipe()
    def kill(self):
        stopped.set()
    def wait(self):
        return 0


subprocess.Popen = Proc
subprocess.run = lambda *a, **k: subprocess.CompletedProcess(a[0], 0, b"", b"")
w.Cam2Stream.IDLE = 0.5
s = w.CAM2_STREAM = w.Cam2Stream()

# a viewer joins: one ffmpeg, whole frames, the camera lock held
s.join()
n, first = s.wait_frame(0)
assert first in frames and procs[0][procs[0].index("-c:v") + 1] == "copy" and procs[0][procs[0].index("-video_size") + 1] == "1280x720"
got = [first]
for _ in range(10):
    n, jpg = s.wait_frame(n)
    got.append(jpg)
assert all(j.startswith(b"\xff\xd8") and j.endswith(b"\xff\xd9") for j in got)          # never a half frame
assert got[:5] == frames[:5], [len(j) for j in got[:5]]                                    # in order, none lost
assert not w._cam2_lock.acquire(blocking=False)                                            # the stream holds the camera
# a still meanwhile: the stream's frame, no second ffmpeg
before = len(procs)
assert w.cam2_frame(max_age=0, wait=1) and len(procs) == before
s.join()                                                                                    # a second viewer: same ffmpeg
assert len(procs) == 1
s.leave()
s.leave()
# the last viewer left: it stops after IDLE and frees the camera
t0 = time.time()
while s.running and time.time() - t0 < 5:
    time.sleep(0.05)
assert not s.running and stopped.is_set()
assert w._cam2_lock.acquire(blocking=False)
w._cam2_lock.release()
assert s.latest() is None                                                                   # stills grab for themselves again

# the route: a multipart MJPEG response until the viewer goes
stopped.clear()
H = next(v for v in vars(w).values() if isinstance(v, type) and hasattr(v, "guard"))
h = H.__new__(H)
h.command, h.path, h.headers = "GET", "/cam2/stream?t=1", {}
h.guard = lambda p: None
h.allowed = lambda: True
class Out(io.BytesIO):
    def write(self, b):
        super().write(b)
        if self.getvalue().count(b"--frame") >= 3:
            raise BrokenPipeError
h.wfile = Out()
h.send_response = lambda code: h.wfile.write(f"HTTP {code}\r\n".encode())
h.send_header = lambda k, v: h.wfile.write(f"{k}: {v}\r\n".encode())
h.end_headers = lambda: h.wfile.write(b"\r\n")
h.route()
body = h.wfile.getvalue()
assert b"multipart/x-mixed-replace; boundary=frame" in body and body.count(b"Content-Type: image/jpeg") >= 2
assert s.viewers == 0                                                                       # leaving is counted even on a broken pipe
print("top view stream check passed")
