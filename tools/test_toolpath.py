"""The live 3D view's small toolpath: outer walls only, polylines, layers; and the page's decoder reads it back.
python tools/test_toolpath.py  (no printer; Node, when installed, runs the page's own decodeToolpath)"""
import io
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import workshop as w  # noqa: E402

g = b"""M83
T1
;TYPE:Outer wall
G1 X10 Y10 Z0.2 F600
G1 X20 Y10 E1
G1 X20 Y20 E1
;TYPE:Sparse infill
G1 X30 Y30 E5
;TYPE:Outer wall
G1 X20 Y20
G1 X10 Y20 E1
G1 X10 Y10 Z0.4
G1 X5 Y10 E1 ; second layer
G1 X5 Y5 E-1
G1 X0 Y5 E0
"""
raw, gz = w.toolpath("t.gcode", read=io.BytesIO(g))
n, nl = struct.unpack_from("<II", raw, 4)
xyz = struct.unpack_from(f"<{3 * n}h", raw, 12)
tools, starts = raw[12 + 6 * n:12 + 7 * n], raw[12 + 7 * n:12 + 8 * n]
lz = struct.unpack_from(f"<{nl}f", raw, 12 + 8 * n)
lp = struct.unpack_from(f"<{nl}I", raw, 12 + 8 * n + 4 * nl)
pts = [tuple(v / 100 for v in xyz[i:i + 3]) for i in range(0, 3 * n, 3)]
# infill is left out; the wall after it is a new polyline (its start point repeats); retracts and travel draw nothing
assert pts == [(10, 10, .2), (20, 10, .2), (20, 20, .2), (20, 20, .2), (10, 20, .2), (10, 10, .4), (5, 10, .4)], pts
assert list(starts) == [1, 0, 0, 1, 0, 1, 0] and set(tools) == {1}, (starts, tools)
assert [round(z, 2) for z in lz] == [0.2, 0.4] and list(lp) == [0, 5], (lz, lp)
assert raw[:4] == b"WTP1" and len(gz) < len(raw) + 40

# everything=True (the Files preview) keeps the infill too
alln = struct.unpack_from("<I", w.toolpath("t.gcode", read=io.BytesIO(g), everything=True)[0], 4)[0]
assert alln == 9, alln   # the infill is its own polyline (a ;TYPE: change starts one): 7 points + 2

# absolute extrusion with G92 resets; a file without ;TYPE: keeps every extrusion
ab = w.toolpath("a.gcode", read=io.BytesIO(b"M82\nG92 E0\nG1 X0 Y0 Z0.2\nG1 X5 Y0 E2\nG92 E0\nG1 X5 Y5 E1\nG1 X9 Y5 E1\n"))[0]
assert struct.unpack_from("<I", ab, 4)[0] == 3, "two extrusions, one polyline (the second E1 after it is no move of plastic)"

# vase mode: one polyline climbing through ;Z: markers still has its layers
vase = b"M83\n;Z:0.2\nG1 X0 Y0 Z0.2\nG1 X10 Y0 Z0.25 E1\n;Z:0.4\nG1 X10 Y10 Z0.35 E1\nG1 X0 Y10 Z0.45 E1\n;Z:0.6\nG1 X0 Y0 Z0.55 E1\n"
vr = w.toolpath("v.gcode", read=io.BytesIO(vase))[0]
vn, vl = struct.unpack_from("<II", vr, 4)
assert (vn, vl) == (5, 3), (vn, vl)
assert list(struct.unpack_from("<3I", vr, 12 + 8 * vn + 4 * vl)) == [0, 1, 3]   # layer 2 starts at point 1 (its first segment 1->2)

# the cache: no modified time = built each time, not kept (it used to raise KeyError); one build for two viewers at once;
# a file over the cap refused; eviction never fails
import threading  # noqa: E402
import time  # noqa: E402
fetches, meta = [], {"size": 100}


def mr(method, path, body=None, headers=None, timeout=30):
    if "metadata" in path:
        return io.BytesIO(json.dumps({"result": meta}).encode())
    fetches.append(path)
    time.sleep(0.2)   # a slow printer: the second viewer arrives while the first reads
    return io.BytesIO(g)


w.moonraker = mr
w._toolpath.clear()
assert w.toolpath("n.gcode") and w.toolpath("n.gcode") and len(fetches) == 2 and not w._toolpath   # no stamp: never cached
meta["modified"], fetches[:] = 1.0, []
ts = [threading.Thread(target=w.toolpath, args=("c.gcode",)) for _ in range(3)]
[t.start() for t in ts]
[t.join() for t in ts]
assert len(fetches) == 1, fetches                       # three viewers on a cold cache: the printer serves it once
for k in range(6):                                      # more files than the cache keeps: the oldest go, nothing raises
    w.toolpath(f"f{k}.gcode")
assert len(w._toolpath) == 4
meta["size"] = w.TOOLPATH_MAX + 1
try:
    w.toolpath("big.gcode")
    raise AssertionError("an oversize file was read")
except ValueError as e:
    assert "MB" in str(e)

node = shutil.which("node")
if node:   # the page's own decoder: segments and layer starts as the 3D view uses them
    html = open(os.path.join(os.path.dirname(__file__), "workshop.html"), encoding="utf-8").read()
    fn = re.search(r"function decodeToolpath\(buf\) \{.*?\n\}\n", html, re.S).group(0)
    js = (fn + "\nconst THREE = {Color: class { constructor(h) { this.r = 1; this.g = 0; this.b = 0; } }};\n"
          "const S = {colours: ['#fff', '#000']};\n"
          "const b = require('fs').readFileSync(process.argv[2]); const g = decodeToolpath(b.buffer.slice(b.byteOffset, b.byteOffset + b.length));\n"
          "console.log(JSON.stringify({count: g.count, layers: g.layers, first: Array.from(g.pos.slice(0, 6)), tools: [...g.tools]}));\n")
    d = tempfile.mkdtemp()
    open(os.path.join(d, "t.bin"), "wb").write(raw)
    open(os.path.join(d, "t.js"), "w").write(js)
    out = json.loads(subprocess.run([node, os.path.join(d, "t.js"), os.path.join(d, "t.bin")], capture_output=True, text=True, check=True).stdout)
    assert out["count"] == 4 and out["tools"] == [1], out                        # 7 points, 3 polylines: 4 segments
    assert [l["start"] for l in out["layers"]] == [0, 3], out                    # layer 2 starts after 3 segments
    assert [round(v, 2) for v in out["first"]] == [10, 10, 0.2, 20, 10, 0.2], out
print("toolpath check passed" + ("" if node else " (no Node: decoder not run)"))
