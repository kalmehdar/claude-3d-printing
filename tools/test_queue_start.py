"""Workshop's queue start: one job starts, it leaves the queue, and the queue is never told to run (starting it and
pausing straight after started nothing, 22 Nov 2026).  python tools/test_queue_start.py"""
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import workshop as w  # noqa: E402

w.record = lambda *a, **k: None
calls = []


def moonraker(method, path, body=None, headers=None, timeout=30):
    calls.append((method, path))
    if path == "/server/job_queue/status":
        return io.BytesIO(json.dumps({"result": {"queued_jobs": [{"job_id": "A1", "filename": "seam test.gcode"},
                                                                  {"job_id": "B2", "filename": "next.gcode"}]}}).encode())
    return io.BytesIO(b'{"result": "ok"}')


w.moonraker = moonraker
H = next(v for v in vars(w).values() if isinstance(v, type) and hasattr(v, "guard"))
h = H.__new__(H)
h.command, h.path, h.headers = "POST", "/moonraker/server/job_queue/start", {"X-Workshop-User": "test"}
sent = []
h.send = lambda code, body=None, *a: sent.append((code, body))
h.guard = lambda path: None
h.allowed = lambda: True
h.body = lambda: b""
h.proxy = lambda *a, **k: calls.append(("PROXY", a[0]))
h.route()
assert sent == [(200, {"result": "ok", "started": "seam test.gcode"})], sent
assert ("DELETE", "/server/job_queue/job?job_ids=A1") in calls and ("POST", "/printer/print/start?filename=seam%20test.gcode") in calls, calls
assert not any("job_queue/start" in p or "job_queue/pause" in p for _, p in calls), calls   # the queue never runs
print("queue start check passed")
