"""Status polls share one printer answer for a second; any action clears it; alerts read the console at most every 10 s.
python tools/test_poll_cache.py  (Moonraker is a stand-in)"""
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import workshop as w  # noqa: E402

calls = []


class R(io.BytesIO):
    status, headers = 200, {"Content-Type": "application/json"}


def moonraker(method, path, body=None, headers=None, timeout=30):
    calls.append((method, path))
    if "gcode_store" in path:
        return R(json.dumps({"result": {"gcode_store": []}}).encode())
    return R(json.dumps({"result": {"n": len(calls)}}).encode())


w.moonraker = moonraker
H = next(v for v in vars(w).values() if isinstance(v, type) and hasattr(v, "guard"))


def ask(method, path):
    h = H.__new__(H)
    h.command, h.path, h.headers = method, path, {}
    out = []
    h.send = lambda code, body=None, *a: out.append((code, body))
    h.body = lambda: b""
    h.proxy(path)
    return out[0]


q = "/printer/objects/query?print_stats"
a, b = ask("GET", q), ask("GET", q)
assert a == b and len(calls) == 1, calls            # two pages within a second: one question to the printer
ask("GET", "/server/files/list?root=gcodes")
ask("GET", "/server/files/list?root=gcodes")
assert len(calls) == 3                              # other reads are never shared
w._poll_cache.clear()                               # what route() does on any POST
assert ask("GET", q) != a and len(calls) == 4       # after an action the printer is asked again
w._poll_cache[q] = (0, 200, b"old", "application/json")
assert ask("GET", q)[1] != b"old"                   # an answer older than POLL_TTL is not used
calls.clear()
w._scanned[0] = 0
w.alerts(); w.alerts()
assert sum("gcode_store" in p for _, p in calls) == 1, calls   # the second page within 10 s reads no console
print("poll cache check passed")

# /api/meta: every file's metadata in one answer, kept per file version, a failure asked again next time
files = {"a.gcode": 1.0, "b.gcode": 2.0, "bad.gcode": 3.0}
seen = []


def moonraker2(method, path, body=None, headers=None, timeout=30):
    seen.append(path)
    if path.startswith("/server/files/list"):
        return R(json.dumps({"result": [{"path": k, "modified": v} for k, v in files.items()]}).encode())
    name = path.split("filename=")[1]
    if name == "bad.gcode":
        raise OSError("printer busy")
    return R(json.dumps({"result": {"estimated_time": len(name), "v": files[name]}}).encode())


w.moonraker = moonraker2
w._meta_cache.clear()
w._list_cache[:] = [0.0, None]
m = w.files_meta(["a.gcode", "b.gcode", "bad.gcode", "gone.gcode", "a.gcode"])
assert m["a.gcode"]["estimated_time"] == 7 and m["bad.gcode"] == {} and "gone.gcode" not in m, m
assert sum("metadata" in p for p in seen) == 3                      # each listed file once, duplicates folded
seen.clear()
w.files_meta(["a.gcode", "b.gcode", "bad.gcode"])
assert [p for p in seen if "metadata" in p] == ["/server/files/metadata?filename=bad.gcode"], seen   # only the failed one again
assert not any(p.startswith("/server/files/list") for p in seen)    # the list is shared for 10 s too
files["a.gcode"] = 9.0                                              # a.gcode re-sliced under the same name (an upload)
w._list_cache[:] = [0.0, None]                                      # what route() does on any POST
seen.clear()
assert w.files_meta(["a.gcode"])["a.gcode"]["v"] == 9.0 and len(seen) == 2
print("files meta check passed")
