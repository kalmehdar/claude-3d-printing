"""Checks design_mcp.py with no slicer, no OpenSCAD and no network beyond localhost.  python tools/test_design_mcp.py"""
import http.server
import json
import os
import struct
import subprocess
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
SPOOLS = [{"id": 3, "remaining_weight": 120.4, "filament": {"material": "PETG", "name": "Black", "color_hex": "1C1C1C", "vendor": {"name": "SUNLU"}}},
          {"id": 4, "remaining_weight": 900, "filament": {"material": "PLA", "name": "White", "color_hex": "F2F2F0"}},
          {"id": 5, "remaining_weight": 999, "archived": True, "filament": {"material": "PETG", "name": "Old"}}]


class Spoolman(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        data = json.dumps(SPOOLS if self.path == "/api/v1/spool" else {}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def session(env, calls):
    msgs = [{"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"}, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}]
    msgs += [{"jsonrpc": "2.0", "id": i + 2, "method": "tools/call", "params": {"name": n, "arguments": a}} for i, (n, a) in enumerate(calls)]
    r = subprocess.run([sys.executable, os.path.join(HERE, "design_mcp.py")], input="\n".join(map(json.dumps, msgs)) + "\n",
                       capture_output=True, text=True, env={**os.environ, **env}, timeout=120)
    replies = {m["id"]: m for m in map(json.loads, r.stdout.splitlines())}
    assert 0 in replies and replies[0]["result"]["serverInfo"]["name"] == "design", r.stderr
    return replies


with tempfile.TemporaryDirectory() as d:
    designs, outside = os.path.join(d, "designs"), os.path.join(d, "elsewhere.scad")
    srv = http.server.HTTPServer(("127.0.0.1", 0), Spoolman)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    stl = os.path.join(d, "cube.stl")   # one triangle is enough for a project
    with open(stl, "wb") as f:
        f.write(b"\0" * 80 + struct.pack("<I", 1) + struct.pack("<12f", 0, 0, 0, 0, 0, 0, 10, 0, 0, 0, 10, 0) + b"\0\0")
    env = {"DESIGN_DIR": designs, "FIT_FILE": os.path.join(d, "fit.json"), "SPOOLMAN_URL": f"http://127.0.0.1:{srv.server_port}"}
    calls = [("design_save", {"path": "box/box.scad", "content": "cube(10);\n"}),
             ("design_save", {"path": outside, "content": "x"}),                      # outside the designs folder: refused
             ("design_save", {"path": "run.py", "content": "x"}),                     # not a design file: refused
             ("design_read", {"path": "box/box.scad"}),
             ("design_files", {}),
             ("design_presets", {}),
             ("design_fit_set", {"slide": 0.2, "press": 0.1, "hinge": 0.4}),
             ("design_fit", {}),
             ("design_fit_set", {"slide": 0.2, "press": 0.1, "hinge": 0.9}),              # outside 0.1 to 0.6: refused by fit.py
             ("design_project", {"parts": [{"label": "Body", "stl": stl, "colour": "#1f1f22"}], "name": "box", "out": "box/box.3mf"}),
             ("design_project", {"parts": [{"stl": stl}], "out": os.path.join(d, "x.3mf")}),   # outside: refused
             ("design_spools", {}),
             ("no_such_tool", {})]
    rep = session(env, calls)
    names = [t["name"] for t in rep[1]["result"]["tools"]]
    assert {"design_save", "design_look", "design_slice", "design_fit", "design_project", "design_spools"} <= set(names), names
    res = {calls[i - 2][0] + str(i): rep[i]["result"] for i in range(2, len(calls) + 2)}
    text = lambda i: rep[i]["result"]["content"][0]["text"]
    err = lambda i: rep[i]["result"].get("isError", False)
    assert not err(2) and open(os.path.join(designs, "box", "box.scad")).read() == "cube(10);\n"
    assert err(3) and "only inside" in text(3) and not os.path.exists(outside)
    assert err(4) and not os.path.exists(os.path.join(designs, "run.py"))
    assert text(5) == "cube(10);\n"   # read back exactly as saved
    assert "box" in text(6) and "box.scad" in text(6)
    assert "strong" in text(7) and "draft" in text(7)
    assert not err(8), text(8)
    assert "hinge_gap = 0.4" in text(9) and "slide_fit = 0.2" in text(9)
    assert err(10) and "0.6" in text(10)
    assert not err(11) and os.path.getsize(os.path.join(designs, "box", "box.3mf")) > 0
    assert err(12) and not os.path.exists(os.path.join(d, "x.3mf"))
    assert "#3" in text(13) and "120 g left" in text(13) and "Old" not in text(13)   # archived spools are left out
    assert err(14)

    sys.path.insert(0, HERE)   # the spool check on a slice summary, in-process
    os.environ["SPOOLMAN_URL"] = env["SPOOLMAN_URL"]
    import importlib
    import design_mcp
    importlib.reload(design_mcp)
    note = design_mcp.spool_check({"materials": ["SUNLU PETG Black", "Generic PLA"], "grams": [150.0, 20.0]})
    assert "needs 150.0 g" in note and "Not enough" in note and "20.0 g of 900 g" in note, note
    srv.shutdown()

import c3dp   # noqa: E402  the one command routes to the scripts and refuses what it does not know
d = json.loads(subprocess.run([sys.executable, os.path.join(HERE, "c3dp.py"), "doctor", "--json"], capture_output=True, text=True).stdout)
assert [c["check"] for c in d][:3] == ["python", "OpenSCAD", "OrcaSlicer"] and all("ok" in c for c in d)
assert subprocess.run([sys.executable, os.path.join(HERE, "c3dp.py"), "settings", "walls"], capture_output=True, text=True).stdout.startswith("walls")
for bad in (["frob"], ["mcp", "nothing"]):
    try:
        c3dp.main(bad)
        raise AssertionError(f"{bad} should be refused")
    except SystemExit as e:
        assert e.code and "c3dp" in str(e.code)
print("design_mcp and c3dp checks passed")
