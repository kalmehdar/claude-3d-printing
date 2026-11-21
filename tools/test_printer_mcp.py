"""Checks printer_mcp.py against a fake Workshop: no printer, no network beyond localhost.  python tools/test_printer_mcp.py"""
import http.server
import json
import os
import subprocess
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
SEEN = []   # (method, path, headers) of every request the fake Workshop got

STATUS = {"status": {
    "print_stats": {"state": "standby", "filename": "", "print_duration": 0, "info": {}, "message": ""},
    "display_status": {"progress": 0}, "heater_bed": {"temperature": 24, "target": 0}, "webhooks": {"state": "ready"},
    "extruder": {"temperature": 25, "target": 0, "state": "PARKED"},
    "print_task_config": {"filament_vendor": ["SUNLU"], "filament_type": ["PETG"], "filament_sub_type": ["generic"],
                          "filament_color_rgba": ["1C1C1CFF"]},
    "filament_feed left": {"extruder0": {"channel_state": "preload_finish"}}}}


class Fake(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def handle_any(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        SEEN.append((self.command, self.path, dict(self.headers), body))
        p = self.path
        if p.startswith("/moonraker/printer/objects/query"):
            return self.reply(200, {"result": STATUS})
        if p.startswith("/api/alerts"):
            return self.reply(200, [])
        if p.startswith("/api/frames"):
            return self.reply(200, {"jobs": ["panel"], "job": "panel", "frames": [
                {"name": f"x-layer{i}.jpg", "url": f"/frames/panel/x-layer{i}.jpg", "time": "06:3%d" % i, "layer": i} for i in range(10)]})
        if p.startswith("/frames/") or p.startswith("/cam.jpg"):
            return self.reply(200, b"\xff\xd8fakejpeg\xff\xd9", "image/jpeg")
        if p.startswith("/moonraker/printer/print/start"):
            return self.reply(409, {"error": "T0 not loaded into the head (preload_finish)"})
        if p.startswith("/api/filament") and self.command == "POST":
            return self.reply(200, {"result": "T0: SUNLU PETG generic #1C1C1C"})
        return self.reply(404, {"error": "not found"})

    do_GET = do_POST = do_DELETE = handle_any


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()
calls = [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}}},
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "printer_status", "arguments": {}}},
    {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "printer_snapshot", "arguments": {}}},
    {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "printer_start", "arguments": {"filename": "a.gcode", "bed_clear": False}}},
    {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "printer_start", "arguments": {"filename": "a.gcode", "bed_clear": True}}},
    {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "printer_set_filament", "arguments": {"head": "T0", "type": "PETG", "colour": "#1c1c1c"}}},
    {"jsonrpc": "2.0", "id": 8, "method": "nonsense/method"},
    {"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": "printer_layer_photos", "arguments": {"count": 3}}},
]
env = dict(os.environ, WORKSHOP_URL=f"http://127.0.0.1:{srv.server_address[1]}", WORKSHOP_USER="Tester")
out = subprocess.run([sys.executable, os.path.join(HERE, "printer_mcp.py")], input="\n".join(json.dumps(c) for c in calls) + "\n",
                     capture_output=True, text=True, env=env, timeout=60).stdout
replies = {r["id"]: r for r in map(json.loads, out.splitlines())}

assert set(replies) == {1, 2, 3, 4, 5, 6, 7, 8, 9}, sorted(replies)               # notifications get no reply
assert replies[1]["result"]["serverInfo"]["name"] == "printer"
names = [t["name"] for t in replies[2]["result"]["tools"]]
assert "printer_start" in names and all(t["inputSchema"]["type"] == "object" for t in replies[2]["result"]["tools"])
status = replies[3]["result"]["content"][0]["text"]
assert "State: standby" in status and "T0: SUNLU PETG generic #1C1C1C, parked at the feeder" in status, status
img = replies[4]["result"]["content"][0]
assert img["type"] == "image" and img["mimeType"] == "image/jpeg"
starts = [s for s in SEEN if "/printer/print/start" in s[1]]
assert len(starts) == 1, "bed_clear=false must not reach the printer; bed_clear=true must"
assert replies[5]["result"]["isError"] and "bed is empty" in replies[5]["result"]["content"][0]["text"]
assert replies[6]["result"]["isError"] and "not loaded" in replies[6]["result"]["content"][0]["text"]   # Workshop's refusal passes through
assert replies[7]["result"]["content"][0]["text"] == "T0: SUNLU PETG generic #1C1C1C"
assert replies[8]["error"]["code"] == -32601
lp = replies[9]["result"]["content"]
assert [c["text"] for c in lp if c["type"] == "text"][:3] == ["panel: layer 0 at 06:30", "panel: layer 4 at 06:34", "panel: layer 9 at 06:39"], lp
assert sum(c["type"] == "image" for c in lp) == 3
posts = [s for s in SEEN if s[0] == "POST"]
assert posts and all(h.get("X-Workshop") == "1" and h.get("X-Workshop-User") == "Tester" for _, _, h, _ in posts)
assert json.loads(next(b for m, p, h, b in posts if p == "/api/filament"))["colour"] == "1c1c1c"
srv.shutdown()

# printer.layer_photo: one photo per layer per job, shared by watch and Workshop
sys.path.insert(0, HERE)
import tempfile, printer
snaps = []
def fake_snap(out):
    snaps.append(out)
    open(out, "wb").write(b"jpg")
    return out
printer.snap = fake_snap
with tempfile.TemporaryDirectory() as d:
    a = printer.layer_photo("sub/Panel.gcode", "3", d)
    b = printer.layer_photo("sub/Panel.gcode", "3", d)        # second caller, same layer: no new photo
    c = printer.layer_photo("sub/Panel.gcode", "4", d)
    assert a == b and a != c and len(snaps) == 2, (a, b, c, snaps)
    assert os.path.dirname(a) == os.path.join(d, "Panel") and a.endswith("-layer3.jpg")
print("printer_mcp.py checks passed")
