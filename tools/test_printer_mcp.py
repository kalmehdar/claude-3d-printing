"""Checks printer_mcp.py against a fake Workshop: no printer, no network beyond localhost.  python tools/test_printer_mcp.py"""
import http.server
import json
import os
import subprocess
import sys
import threading
import time

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
        if p.startswith("/api/photo-checks"):
            return self.reply(200, {"enabled": True, "every": 25, "checks": []})
        if p.startswith("/api/spools"):
            return self.reply(200, {"low_g": 200, "heads": {}})
        if p.startswith("/api/bed"):
            return self.reply(200, {"state": "printing"})
        if p.startswith("/api/calibration") and self.command == "POST":
            return self.reply(201, {"test": "temperature", "value": "240", "filament": "SUNLU PETG", "key": "nozzle_temperature", "photo": "/x.jpg"})
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
    {"jsonrpc": "2.0", "id": 10, "method": "tools/call", "params": {"name": "printer_calibration", "arguments": {"test": "temperature", "value": "240", "filament": "SUNLU PETG"}}},
]
env = dict(os.environ, WORKSHOP_URL=f"http://127.0.0.1:{srv.server_address[1]}", WORKSHOP_USER="Tester")
run = subprocess.run([sys.executable, os.path.join(HERE, "printer_mcp.py")], input="\n".join(json.dumps(c) for c in calls) + "\n",
                     capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
out = run.stdout
assert out.strip(), "printer_mcp.py gave no replies: " + run.stderr[-800:]
replies = {r["id"]: r for r in map(json.loads, out.splitlines())}

assert set(replies) == {1, 2, 3, 4, 5, 6, 7, 8, 9, 10}, sorted(replies)               # notifications get no reply
assert replies[1]["result"]["serverInfo"]["name"] == "printer"
names = [t["name"] for t in replies[2]["result"]["tools"]]
assert "printer_start" in names and all(t["inputSchema"]["type"] == "object" for t in replies[2]["result"]["tools"])
status = replies[3]["result"]["content"][0]["text"]
assert "State: standby" in status and "T0: SUNLU PETG generic #1C1C1C, parked at the feeder" in status, status
img = replies[4]["result"]["content"][0]
assert img["type"] == "image" and img["mimeType"] == "image/jpeg"
starts = [s for s in SEEN if "/printer/print/start" in s[1]]
assert len(starts) == 1, "bed_clear=false must not reach the printer; bed_clear=true must"
beds = [x for x in SEEN if x[0] == "POST" and x[1].startswith("/api/bed")]
assert len(beds) == 1, "bed_clear=true marks the bed clear in Workshop before the start; bed_clear=false does not"
assert replies[5]["result"]["isError"] and "bed is empty" in replies[5]["result"]["content"][0]["text"]
assert replies[6]["result"]["isError"] and "not loaded" in replies[6]["result"]["content"][0]["text"]   # Workshop's refusal passes through
assert replies[7]["result"]["content"][0]["text"] == "T0: SUNLU PETG generic #1C1C1C"
assert replies[8]["error"]["code"] == -32601
lp = replies[9]["result"]["content"]
assert [c["text"] for c in lp if c["type"] == "text"][:3] == ["panel: layer 0 at 06:30", "panel: layer 4 at 06:34", "panel: layer 9 at 06:39"], lp
assert sum(c["type"] == "image" for c in lp) == 3
assert replies[10]["result"]["content"][0]["text"].startswith("Recorded temperature = 240 for SUNLU PETG with a photo"), replies[10]
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
printer.call = lambda path, *a, **k: {"jobs": [{"job_id": "000042", "filename": "sub/Panel.gcode", "status": "in_progress"}]}
with tempfile.TemporaryDirectory() as d:
    a = printer.layer_photo("sub/Panel.gcode", "3", d)
    b = printer.layer_photo("sub/Panel.gcode", "3", d)        # second caller, same layer: no new photo
    c = printer.layer_photo("sub/Panel.gcode", "4", d)
    assert a == b and a != c and len(snaps) == 2, (a, b, c, snaps)
    assert os.path.dirname(a) == os.path.join(d, "Panel-000042") and a.endswith("-layer3.jpg")
    old = os.path.join(d, "Old-000001"); os.makedirs(old)
    os.utime(old, (time.time() - 31 * 86400,) * 2)
    assert printer.prune_photos(30, d) == ["Old-000001"] and os.path.isdir(os.path.join(d, "Panel-000042"))   # 30 days, newer kept
# watch's cold-bed pause: mid-print only, never in the end macro or before the first layer
assert printer.bed_off_midprint({"state": "printing", "bed": "40/0", "layer": "30/675"})
assert not printer.bed_off_midprint({"state": "printing", "bed": "78/0", "layer": "675/675"})   # PRINT_END turned it off
assert not printer.bed_off_midprint({"state": "printing", "bed": "30/0", "layer": "0/675"})
assert not printer.bed_off_midprint({"state": "printing", "bed": "80/80", "layer": "30/675"})
assert not printer.bed_off_midprint({"state": "printing", "bed": "40/0", "layer": None})
# photo folders are URL- and path-safe whatever the file is called
printer.call = lambda path, *a, **k: {"jobs": [{"job_id": "000009", "filename": ".Benchy (2) é.gcode", "status": "in_progress"}]}
assert printer.run_folder(".Benchy (2) é.gcode") == "Benchy__2___-000009", printer.run_folder(".Benchy (2) é.gcode")
# Workshop: one profile per colour -> the nearest colour wins
import workshop
assert workshop.rgb_gap("#1A1A1A", "1C1C1C") < workshop.rgb_gap("#F2F2F2", "1C1C1C") < workshop.rgb_gap(None, "1C1C1C")
# Workshop: prices per kg from a filament register; unpriced rows skipped, first priced row per material wins
with tempfile.TemporaryDirectory() as d:
    reg = os.path.join(d, "filament.md")
    open(reg, "w", encoding="utf-8").write("# Stock\n\n| Spool | Material | Colour | Price per kg |\n| --- | --- | --- | --- |\n"
                         "| SnapSpeed | PLA | Blue | from the receipt |\n| SUNLU | PETG | Black | £7.86 (4 kg bundle) |\n"
                         "| SUNLU | PETG | Red | £9.00 |\n")
    assert workshop.filament_prices(reg) == {"PETG": 7.86}, workshop.filament_prices(reg)
    assert workshop.filament_prices(os.path.join(d, "none.md")) == {} and workshop.filament_prices(None) == {}
# camera: follows what Moonraker lists, stock still on Moonraker's port, Extended Firmware's snapshot on port 80
printer.BASE, printer.CAMERA = "http://10.0.0.5:7125", None
for listed, want in ((None, "http://10.0.0.5:7125/server/files/camera/monitor.jpg"),
                     ("/server/files/camera/monitor.jpg", "http://10.0.0.5:7125/server/files/camera/monitor.jpg"),
                     ("/webcam/snapshot.jpg", "http://10.0.0.5/webcam/snapshot.jpg")):
    printer._cam.update(url=None, t=0)
    printer.call = lambda path, *a, l=listed, **k: {"webcams": [{"enabled": True, "snapshot_url": l}] if l else []}
    assert printer.camera_url() == want, (listed, printer.camera_url())
printer._cam.update(url=None, t=0)
printer.call = lambda path, *a, **k: {"webcams": [{"enabled": True, "snapshot_url": "/server/files/camera/monitor.jpg"},
                                                  {"enabled": True, "snapshot_url": "/webcam/snapshot.jpg"}]}
assert printer.camera_url() == "http://10.0.0.5/webcam/snapshot.jpg"   # the old stock entry left behind loses
printer.CAMERA = "http://cam.local/x.jpg"
assert printer.camera_url() == "http://cam.local/x.jpg"   # an explicit camera always wins
# Workshop calibration store: refuses unknown tests and empty values, records with the profile key and a photo
with tempfile.TemporaryDirectory() as d:
    workshop.CAL_LOG, workshop.CAL_PHOTOS = os.path.join(d, "cal.jsonl"), os.path.join(d, "cal")
    workshop.CFG.update(moonraker="http://10.0.0.5:7125", camera=None)
    workshop.record = lambda *a, **k: None
    printer.snap = fake_snap
    for bad in ({"test": "nonsense", "value": "1"}, {"test": "temperature", "value": " "}):
        try:
            workshop.calibration(bad)
            raise AssertionError("accepted " + str(bad))
        except ValueError:
            pass
    row = workshop.calibration({"test": "temperature", "value": "240", "filament": "SUNLU PETG white"}, who="Tester")[0]
    assert row["key"] == "nozzle_temperature" and row["value"] == "240" and row["photo"].startswith("/calibration-photo/")
    assert os.path.isfile(os.path.join(workshop.CAL_PHOTOS, row["photo"].rsplit("/", 1)[1]))
# photo watch: pauses only when two looks agree on a clear failure with high confidence; cosmetic faults never pause
with tempfile.TemporaryDirectory() as d:
    workshop.PHOTO_LOG = os.path.join(d, "checks.jsonl")
    workshop.time.sleep = lambda s: None
    printer.snap = fake_snap
    printer.status = lambda: {"state": "printing"}
    pauses, pushes = [], []
    class _R:
        def read(self): return b"{}"
    workshop.moonraker = lambda method, path, **k: (pauses.append(path), _R())[1]
    workshop.record = lambda *a, **k: None
    printer.push = lambda *a, **k: pushes.append(a)
    photo = os.path.join(d, "x-layer50.jpg"); open(photo, "wb").write(b"jpg")
    def verdicts(*vs):
        it = iter(vs)
        workshop.look_at_photo = lambda jpg, layer, plan=None: dict(next(it))
    bad = {"verdict": "pause", "problem": "spaghetti", "confidence": 0.95, "reason": "tangles"}
    ok = {"verdict": "ok", "problem": "none", "confidence": 0.9, "reason": "fine"}
    verdicts(bad, ok); workshop.photo_watch("a.gcode", 50, photo)
    assert not pauses, "one look is not enough"
    verdicts({**bad, "problem": "other"}); workshop.photo_watch("a.gcode", 50, photo)
    assert not pauses, "a cosmetic fault never pauses"
    verdicts({**bad, "confidence": 0.6}); workshop.photo_watch("a.gcode", 50, photo)
    assert not pauses, "unsure never pauses"
    verdicts(bad, bad); workshop.photo_watch("a.gcode", 50, photo)
    assert pauses == ["/printer/print/pause"] and pushes, (pauses, pushes)
    assert len(open(workshop.PHOTO_LOG).read().splitlines()) == 6
# API keys: env first, then the files in order; a file others can read is skipped
with tempfile.TemporaryDirectory() as d:
    workshop.API_KEY_FILE = os.path.join(d, "anthropic-key")
    os.environ.pop("ANTHROPIC_API_KEY", None)
    for name, val, mode in (("anthropic-key", "k1", 0o600), ("anthropic-key-2", "k2", 0o600), ("anthropic-key-3", "k3", 0o644)):
        f = os.path.join(d, name); open(f, "w").write(val + "\n"); os.chmod(f, mode)
    # Windows has no Unix mode bits (chmod 600 leaves 666), so only the order is checked there
    want = ["k1", "k2"] if os.name != "nt" else []
    assert os.name == "nt" or workshop.api_keys() == want, workshop.api_keys()
# photo watch remembers across restarts: the log says block 12 of a.gcode was checked
with tempfile.TemporaryDirectory() as d:
    workshop.PHOTO_LOG = os.path.join(d, "c.jsonl")
    open(workshop.PHOTO_LOG, "w").write('{"file": "a.gcode", "layer": 318}\n{"file": "b.gcode", "layer": 40}\n')
    assert workshop.checked_block("a.gcode") == 12 and workshop.checked_block("b.gcode") == 1 and workshop.checked_block("c.gcode") == -1
# Workshop guard, offline (never test a guard with a real command): speed 50-150 and the light pass mid-print, motion doesn't
class _G:
    command = "POST"
    printer_busy = lambda self: True   # a job is running
    body = lambda self: None           # the command is in the address, as every Workshop client sends it
    guard = workshop.Handler.guard
g = _G()
for script, ok in (("M220 S115", True), ("M220 S50", True), ("M220 S200", False), ("M220 S20", False),
                   ("SET_LED LED=cavity_led WHITE=1", True), ("G28", False), ("M104 S250", False),
                   ("SET_PAUSE_AT_LAYER LAYER=120", True), ("SET_PAUSE_AT_LAYER ENABLE=0", True), ("SET_PAUSE_AT_LAYER LAYER=5 MACRO=G28", False),
                   ("SET_PAUSE_AT_LAYER MACRO=G28", False)):
    why = g.guard("/printer/gcode/script?script=" + workshop.urllib.parse.quote(script))
    assert (why is None) == ok, (script, why)
# slicer accuracy from history (the real fit test and plaque numbers of 21 Nov 2026)
acc = workshop.accuracy([
    {"status": "completed", "filename": "fit.gcode", "print_duration": 2280, "total_duration": 2580, "filament_used": 6686,
     "metadata": {"estimated_time": 2280, "filament_total": 6689.31, "filament_type": "PETG"}},
    {"status": "completed", "filename": "plaque.gcode", "print_duration": 7200, "total_duration": 7320, "filament_used": 12967,
     "metadata": {"estimated_time": 6600, "filament_total": 12932.6, "filament_type": "PLA;PLA;PLA;PLA"}},
    {"status": "cancelled", "filename": "x.gcode", "print_duration": 60, "total_duration": 600, "filament_used": 669,
     "metadata": {"estimated_time": 2280, "filament_total": 6689.31}}])
assert acc["n"] == 2 and acc["jobs"][0]["time"] == 1.0 and acc["jobs"][0]["prep_min"] == 5.0
assert acc["jobs"][1]["time"] == 1.091 and acc["jobs"][1]["colours"] == 4 and acc["time"] == 1.091
# spools: the printer's measured total split by the slicer's per-head share (the panel's real metadata, 21 Nov 2026)
panel = {"filament_used_mm": [1227.1, 84557.68, 0.0, 0.0], "filament_weight": [3.69, 254.23, 0.0, 0.0]}
assert workshop.split_use(85784.78, panel) == {"T0": 3.7, "T1": 254.2}, workshop.split_use(85784.78, panel)
assert workshop.split_use(42892.39, panel) == {"T0": 1.8, "T1": 127.1}   # cancelled halfway: half of each
with tempfile.TemporaryDirectory() as d:
    workshop.SPOOLS = os.path.join(d, "spools.json")
    workshop.spools({"T0": {"spool": "white", "start_g": 1000, "used_g": 0}, "T1": {"spool": "black", "start_g": 400, "used_g": 20}})
    pushes.clear()
    workshop.take_off_spools("panel.gcode", 85784.78, panel)
    sp = workshop.spools()
    assert sp["T0"]["used_g"] == 3.7 and sp["T1"]["used_g"] == 274.2, sp
    assert pushes and "T1 black" in pushes[-1][1], pushes   # 400 - 274 = 126 g left: under 200, warned once
# bed handover: a finished (or cancelled) print leaves a part waiting; a new print means someone said the bed was clear
with tempfile.TemporaryDirectory() as d:
    workshop.BED, workshop.SPOOLS = os.path.join(d, "bed.json"), os.path.join(d, "spools.json")
    class _M:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps({"result": {}}).encode()
    workshop.moonraker = lambda *a, **k: _M()
    workshop.alert_change("printing", {"state": "complete", "file": "panel.gcode", "printing_min": 300, "filament_used_m": 85.8})
    assert workshop.bed()["state"] == "part waiting" and workshop.bed()["file"] == "panel.gcode"
    workshop.alert_change("standby", {"state": "printing", "file": "next.gcode"})
    assert workshop.bed()["state"] == "printing"
    workshop.alert_change("printing", {"state": "cancelled", "file": "next.gcode", "printing_min": 5, "filament_used_m": 1})
    assert workshop.bed()["state"] == "part waiting"
# print requests: ask, take, finish; bad input refused
with tempfile.TemporaryDirectory() as d:
    workshop.REQUESTS = os.path.join(d, "requests.json")
    r = workshop.requests_({"what": "Switch rack mount", "qty": 2, "colour": "red"}, who="Mohamed")[0]
    assert r["id"] == 1 and r["state"] == "open" and r["who"] == "Mohamed"
    workshop.requests_({"id": 1, "state": "taken"}, who="Khalid")
    assert workshop.requests_()[0]["state"] == "taken" and workshop.requests_()[0]["by"] == "Khalid"
    for bad in ({"what": " "}, {"what": "x", "qty": 99}, {"id": 7, "state": "done"}, {"id": 1, "state": "eaten"}):
        try:
            workshop.requests_(bad, who="x"); raise AssertionError(str(bad))
        except ValueError:
            pass
# print log: one row appended to the end of the register's table, '?' where only a person knows
with tempfile.TemporaryDirectory() as d:
    reg = os.path.join(d, "prints.md")
    open(reg, "w", encoding="utf-8").write("# Print log\n\n| Date | Model (file) | For | Material and heads | Settings | Grams | Time | Cost | Outcome | KeyProof? |\n"
                         "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n| 2026-11-20 | old.gcode | ? | ? | ? | 1 | 1 min | ? | Completed | No |\n\nNotes after the table.\n")
    fil = os.path.join(d, "filament.md"); open(fil, "w", encoding="utf-8").write("| Material | Price |\n| --- | --- |\n| PETG | £12.00 per kg |\n")
    workshop.SPOOLS = os.path.join(d, "spools.json")
    json.dump({"T1": {"spool": "SUNLU PETG black", "start_g": 1000, "used_g": 0}}, open(workshop.SPOOLS, "w"))
    workshop.CFG.update(print_log=None, register=fil)
    assert workshop.log_print({"state": "complete", "file": "a.gcode"}, {}, {"T1": 10}) is None   # no register set: nothing written
    workshop.CFG["print_log"] = reg
    row = workshop.log_print({"state": "complete", "file": "panel-quality.gcode", "printing_min": 125.4},
                             {"filament_type": "PETG;PETG"}, {"T1": 250.0}, folder="/x/panel-quality-000009")
    lines = open(reg, encoding="utf-8").read().split("\n")
    assert lines[5] == row and lines[6] == "" and "Notes after the table." in lines, lines
    assert row == "| " + workshop.time.strftime("%Y-%m-%d") + " | panel-quality.gcode | ? | SUNLU PETG black on T1 | quality | 250.0 (measured) | 125 min | £3.00 | Completed; layer photos panel-quality-000009 (timelapse.mp4 beside them once made) | ? |", row
# cancel one part: only a part still printing, never the last one left, and only that one command is sent
sent = []
class _OK:
    def read(self): return b"{}"
workshop.moonraker = lambda method, path, **k: (sent.append(path), _OK())[1]
def fake_parts(left, state="printing"):
    workshop.parts = lambda: {"file": "bins.gcode", "state": state, "objects": [{"name": n} for n in left], "excluded": [], "current": left[0] if left else None, "left": left}
fake_parts(["A", "B"])
assert workshop.cancel_part("A B", "x")[0] == 400 and workshop.cancel_part("C", "x")[0] == 400 and not sent
fake_parts(["A"])
code, body = workshop.cancel_part("A", "x")
assert code == 409 and "only part left" in body["error"] and not sent, body
fake_parts(["A", "B"], state="standby")
assert workshop.cancel_part("A", "x")[0] == 409 and not sent
fake_parts(["A", "B"])
code, body = workshop.cancel_part("B", "x")
assert code == 200 and body["left"] == ["A"] and sent == ["/printer/gcode/script?script=EXCLUDE_OBJECT%20NAME%3DB"], (code, body, sent)
# the Claude app's cancel tool refuses without the person's yes and sends nothing
import printer_mcp
_calls = []
printer_mcp.http = lambda m, path, body=None, **k: (_calls.append((m, path, body)), {"cancelled": "B", "left": ["A"]})[1]
for args in ({"name": "B"}, {"name": "B", "person_said_yes": "yes"}):
    try:
        printer_mcp.t_cancel_part(args); raise AssertionError(args)
    except ValueError:
        pass
assert not _calls
assert printer_mcp.t_cancel_part({"name": "B", "person_said_yes": True}).startswith("Cancelled B") and _calls == [("POST", "/api/parts", {"name": "B"})], _calls
# printer.py logcheck: events counted, each tied to the restart before it; backup leaves out serial, agent and factory copies
f = printer.scan_log(["Start printer at Fri Nov 21 23:36:03 2026 (1.0 2.0)", "Stats 10.0: gcodein=0",
                      "MCU 'mcu' shutdown: Timer too close", "Start printer at Sat Nov 22 00:10:00 2026 (3.0 4.0)", "ok"])
assert f["restart"]["count"] == 2 and not f["restart"]["serious"], f
assert f["mcu shutdown"]["after_restart"].startswith("Fri Nov 21") and f["timer too close"]["serious"], f
assert printer.scan_log(["Stats 1.0: all fine"]) == {}
for path, left_out in (("persistent/agent.json", True), ("snapmaker/product_info.json", True), ("factory_v2.cfg", True),
                       ("printer-20261120_135204.cfg", True), (".moonraker.conf.bkp", True), ("printer.cfg", False),
                       ("snapmaker/input_shaper.json", False), ("extended/moonraker/02_internal_camera.cfg", False)):
    assert bool(printer.BACKUP_SKIP.search(path)) == left_out, path
# uploaded models: only .stl/.3mf, a size limit and a hex colour; saved under uploads, then the shared slice path
with tempfile.TemporaryDirectory() as d:
    workshop.UPLOADS = d
    seen = []
    _sp = workshop.slice_parts
    workshop.slice_parts = lambda dd, parts, req: (seen.append((dd, parts, req)), (200, {"ok": True}))[1]
    assert workshop.slice_upload("x.gcode", b"1", {})[0] == 400
    assert workshop.slice_upload("x.stl", b"", {})[0] == 400
    assert workshop.slice_upload("x.stl", b"solid", {"colour": "red"})[0] == 400
    code, _ = workshop.slice_upload("../../evil name.stl", b"solid x", {"colour": "1c1c1c", "preset": "lean"})
    assert code == 200 and os.path.dirname(seen[-1][1][0][1]) == d and seen[-1][1][0][2] == "#1c1c1c", seen[-1]
    workshop.slice_parts = _sp
# the Claude app's controls: home needs the person's word that nothing is in the way; unknown actions and files refused
import printer_mcp as _pm
_sent = []
_pm.mr = lambda m, path, **k: _sent.append(path)
for _bad in ({"action": "home"}, {"action": "dance"}):
    try:
        _pm.t_control(_bad); raise AssertionError(_bad)
    except ValueError:
        pass
assert not _sent
assert _pm.t_control({"action": "home", "nothing_in_the_way": True}) == "Homed." and _sent == ["/printer/gcode/script?script=G28"], _sent
for _bad in ({"path": "/etc/passwd"}, {"path": "/nope/part.stl"}):
    try:
        _pm.t_upload_model(_bad); raise AssertionError(_bad)
    except ValueError:
        pass
# the page's own script must parse (a stray // once cut a one-line function in half and the whole page stopped)
import re as _re, shutil as _sh
if _sh.which("node"):
    _js = max(_re.findall(r"<script>(.*?)</script>", open(os.path.join(os.path.dirname(__file__), "workshop.html"), encoding="utf-8").read(), _re.S), key=len)
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as _f:
        _f.write(_js)
    _r = subprocess.run(["node", "--check", _f.name], capture_output=True, text=True)
    os.remove(_f.name)
    assert _r.returncode == 0, "workshop.html script does not parse: " + _r.stderr[-300:]
# Workshop's daily check: idle only, once per 20 h, backup into --config-backup, a push only for a new log fault
with tempfile.TemporaryDirectory() as d:
    workshop.DAILY = os.path.join(d, "daily.json")
    workshop.CFG["config_backup"] = os.path.join(d, "cfg")
    pushed, backed = [], []
    printer.backup = lambda folder: (backed.append(folder), {"changed": ["printer.cfg"], "same": 0, "skipped": []})[1]
    printer.push = lambda *a, **k: pushed.append(a)
    workshop.record = lambda *a, **k: None
    st = {"s": "printing"}
    printer.state = lambda: st["s"]
    log = {"found": {}}
    printer.logcheck = lambda mb=3: ("klippy.log", log["found"])
    assert workshop.daily_printer_check(now=1000.0) is None and not backed   # mid-print: waits
    st["s"] = "standby"
    r = workshop.daily_printer_check(now=1000.0)
    assert r["changed"] == ["printer.cfg"] and backed and not pushed, r
    assert workshop.daily_printer_check(now=1000.0 + 3600) is None   # within 20 h: no second run
    log["found"] = {"mcu shutdown": {"count": 1, "serious": True, "last": "MCU 'mcu' shutdown", "after_restart": "x"}}
    workshop.daily_printer_check(now=1000.0 + 21 * 3600)
    assert len(pushed) == 1, pushed
    workshop.daily_printer_check(now=1000.0 + 42 * 3600)
    assert len(pushed) == 1, "the same fault is not pushed again"
print("printer_mcp.py checks passed")
