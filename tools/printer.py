"""Drive a Klipper/Moonraker printer from the command line: what Claude uses to send, start, watch and stop prints.

    python printer.py status                      state, layer, progress, time left, temperatures, loaded filament
    python printer.py snap [out.jpg]              the camera's current frame (look at it before every start)
    python printer.py files                       G-code on the printer, newest first
    python printer.py send part.gcode [--queue]   upload (and add to the job queue); never starts anything
    python printer.py start part.gcode --bed-clear   start now; the flag says the bed was seen empty (camera or a person)
    python printer.py queue [add FILE | remove ID | start --bed-clear | hold]
    python printer.py pause | resume | cancel
    python printer.py light on|off                chamber light (Snapmaker U1: cavity_led)
    python printer.py unload T2 | all             Snapmaker U1: unload a head's filament back to the spool (its own feeder routine)
    python printer.py filament T2 PETG --vendor Polymaker --sub generic --colour 1f1f22
                                                  Snapmaker U1: tell the printer what was loaded on a head (as its screen does)
    python printer.py filament list               the types, vendors and sub-types the printer knows
    python printer.py gcode "CMD" [--force]       raw G-code; refused while a job runs unless --force
    python printer.py watch [secs] [--frames DIR] poll until the job ends, one camera frame per layer into DIR
    python printer.py history [n]
    python printer.py notify "Title" ["text"] [--level good|warn|bad]   post to Discord (watch posts when a job ends)

The printer: --printer ADDRESS, or PRINTER_HOST, or {"host": ...} in ~/.config/claude-3d-printing/printer.json.
The camera: --camera PATH on Moonraker (default /server/files/camera/monitor.jpg, the Snapmaker U1's still).
Discord: a channel webhook URL in PRINTER_DISCORD_WEBHOOK or "discord_webhook" in printer.json (keep it out of git);
"discord_mention" (e.g. <@123>) is added to warn and bad posts.
Moonraker often has no password: use it on a home network or over a VPN only. Standard library only.
"""
import argparse
import base64
import json
import os
import socket
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

CONF = os.path.join(os.path.expanduser("~"), ".config", "claude-3d-printing", "printer.json")
RUNNING = ("printing", "paused")
BASE = CAMERA = None
CFG = {}
LEVEL = {"info": 1920728, "good": 955268, "warn": 11880200, "bad": 11805464}


def notify(title, text="", level="info"):
    """Post a Discord embed if a webhook is set up: True posted, False failed, None when no webhook is set."""
    url = os.environ.get("PRINTER_DISCORD_WEBHOOK") or CFG.get("discord_webhook")
    if not url:
        return None
    body = {"embeds": [{"title": title[:250], "description": text[:3000], "color": LEVEL.get(level, LEVEL["info"]),
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}]}
    if level in ("warn", "bad") and CFG.get("discord_mention"):
        body["content"] = CFG["discord_mention"]
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "claude-3d-printing/printer.py"})
    try:
        urllib.request.urlopen(req, timeout=10).read()
        return True
    except OSError as e:
        print("discord post failed:", getattr(e, "code", e), file=sys.stderr)   # never echo the URL
        return False


def call(path, method="GET", body=None, timeout=15, raw=False):
    req = urllib.request.Request(BASE + path, data=body, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    return data if raw else json.loads(data).get("result")


def query(*objs):
    return call("/printer/objects/query?" + "&".join(urllib.parse.quote(o) for o in objs))["status"]


def state():
    return query("print_stats")["print_stats"]["state"]


def status():
    names = call("/printer/objects/list")["objects"]
    heads = [o for o in names if o == "extruder" or (o.startswith("extruder") and o[8:].isdigit())]
    s = query("print_stats", "display_status", "heater_bed", "webhooks", *heads,
              *(["print_task_config"] if "print_task_config" in names else []))
    ps, ds = s["print_stats"], s.get("display_status", {})
    info, p = ps.get("info") or {}, ds.get("progress") or 0
    out = {"klippy": s["webhooks"]["state"], "state": ps["state"], "file": ps.get("filename") or None,
           "progress_pct": round(100 * p, 1),
           "layer": f'{info["current_layer"]}/{info["total_layer"]}' if info.get("total_layer") else None,
           "printing_min": round(ps.get("print_duration", 0) / 60, 1),
           "filament_used_m": round(ps.get("filament_used", 0) / 1000, 2),
           "bed": f'{s["heater_bed"]["temperature"]:.0f}/{s["heater_bed"]["target"]:.0f}',
           "heads": {h: f'{s[h]["temperature"]:.0f}/{s[h]["target"]:.0f} {s[h].get("state", "")}'.strip() for h in heads}}
    if ps["state"] == "printing" and p > 0.02:
        left = ps["print_duration"] * (1 - p) / p
        out["left_min"] = round(left / 60)
        out["finishes"] = time.strftime("%H:%M", time.localtime(time.time() + left))
    t = s.get("print_task_config")
    if t:   # Snapmaker U1: what is loaded on each head
        out["slots"] = [f"T{i} {ty} {sub} #{c[:6]}" for i, (ty, sub, c) in enumerate(zip(
            t.get("filament_type", []), t.get("filament_sub_type", []), t.get("filament_color_rgba", [])))]
    if ps.get("message"):
        out["message"] = ps["message"]
    return out


def ws_rpc(host, port, method, params, timeout=15):
    """One JSON-RPC call over Moonraker's websocket (some endpoints, like the U1's camera, are websocket-only)."""
    s = socket.create_connection((host, port), timeout=timeout)
    deadline = time.time() + timeout   # status pushes keep a socket busy, so time the whole call, not each read
    try:
        key = base64.b64encode(os.urandom(16)).decode()
        s.sendall(f"GET /websocket HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                  f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                raise OSError("websocket closed during handshake")
            buf += chunk
        if b" 101 " not in buf.split(b"\r\n", 1)[0]:
            raise OSError("no websocket upgrade")
        buf = buf.split(b"\r\n\r\n", 1)[1]
        msg = json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": 4242}).encode()
        n = len(msg)
        hdr = bytes([0x81]) + (bytes([0x80 | n]) if n < 126 else bytes([0xFE]) + struct.pack(">H", n))
        mask = os.urandom(4)
        s.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(msg)))
        while True:   # read frames until our reply (Moonraker also pushes status notifications)
            if time.time() > deadline:
                raise OSError("no reply from the printer in time")
            while len(buf) < 2:
                chunk = s.recv(65536)
                if not chunk:
                    raise OSError("websocket closed")
                buf += chunk
            ln, off = buf[1] & 0x7F, 2
            if ln == 126:
                ln, off = struct.unpack(">H", buf[2:4])[0], 4
            elif ln == 127:
                ln, off = struct.unpack(">Q", buf[2:10])[0], 10
            while len(buf) < off + ln:
                chunk = s.recv(65536)
                if not chunk:
                    raise OSError("websocket closed")
                buf += chunk
            payload, buf = buf[off:off + ln], buf[off + ln:]
            try:
                d = json.loads(payload)
            except ValueError:
                continue
            if d.get("id") == 4242:
                if "error" in d:
                    raise OSError(d["error"].get("message", "camera error"))
                return d.get("result")
    finally:
        s.close()


def camera_age():
    """Seconds since the camera still was written, or None when the printer does not list a camera folder."""
    try:
        files = call("/server/files/list?root=camera", timeout=5)
    except OSError:
        return None
    hit = next((f for f in files if CAMERA.endswith("/" + f["path"])), None)
    return time.time() - hit["modified"] if hit else None


def wake_camera():
    """Snapmaker U1: the still only refreshes while something 'watches' (its app sends camera.start_monitor).
    Ask for it when the still is stale, then wait for a fresh frame. Harmless on printers without it."""
    age = camera_age()
    if age is None or age < 10:
        return
    host, port = BASE[len("http://"):].rsplit(":", 1)
    try:
        ws_rpc(host, int(port), "camera.start_monitor", {"req_id": 1, "domain": "lan", "interval": 1})
    except OSError:
        return
    for _ in range(10):
        time.sleep(0.6)
        if (camera_age() or 99) < 3:
            return


def snap(out):
    wake_camera()
    open(out, "wb").write(call(CAMERA, raw=True, timeout=15))
    return out


def queue_running():
    return call("/server/job_queue/status")["queue_state"] in ("ready", "loading")


def send(path, queue=False):
    name = os.path.basename(path)
    if not name.lower().endswith((".gcode", ".gco", ".g")):
        sys.exit("refused: send sliced G-code only")
    b = uuid.uuid4().hex
    body = (f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n").encode() + open(path, "rb").read() + f"\r\n--{b}--\r\n".encode()
    req = urllib.request.Request(BASE + "/server/files/upload", data=body, method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={b}"})
    with urllib.request.urlopen(req, timeout=600) as r:
        r.read()
    if queue:
        if queue_running():   # it would start onto the last print with no bed check
            sys.exit(f"{name} is on the printer but not queued: the queue is running; `queue hold` first")
        call("/server/job_queue/job?filenames=" + urllib.parse.quote(name), "POST")
    return name


def gcode(cmd, force=False):
    if not force and state() in RUNNING:
        sys.exit("refused: a job is running; --force only for harmless commands")
    return call("/printer/gcode/script?script=" + urllib.parse.quote(cmd), "POST", timeout=300)


def head_obj(i):
    return "extruder" if i == 0 else f"extruder{i}"


def channel_state(i):
    """Snapmaker U1 feeder state for head i (e.g. unload_finish), from the filament_feed modules."""
    for mod in ("filament_feed left", "filament_feed right"):
        ch = query(mod).get(mod, {}).get(f"extruder{i}")   # the feeders name every head extruderN, T0 included
        if ch:
            return ch.get("channel_state")
    return None


def run_waiting(cmd, done, limit):
    """Send one G-code command; the U1 holds the request open while it works and sometimes drops it, so success is
    judged by the printer's state (done() returning True), never by the HTTP reply."""
    try:
        call("/printer/gcode/script?script=" + urllib.parse.quote(cmd), "POST", timeout=limit)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{cmd}: " + json.loads(e.read() or b"{}").get("error", {}).get("message", str(e)))
    except OSError:
        pass
    end = time.time() + limit
    while time.time() < end:
        if done():
            return
        time.sleep(2)
    raise SystemExit(f"{cmd}: the printer did not finish in {limit} s; check it")


def unload(which):
    """Snapmaker U1: unload heads exactly as its screen's Unload all does (recorded 21 Nov 2026): AUTO_FEEDING_BATCH
    ACTION=START; per head DOING STAGE=prepare (pick the head, heat it, preheat the next) then DOING STAGE=doing
    (cut, retract, wind back); ACTION=STOP at the end, which turns the heaters off."""
    if state() in RUNNING:
        sys.exit("refused: a job is running; unload after it ends")
    t = query("print_task_config").get("print_task_config")
    if not t or "filament_exist" not in t:
        sys.exit("this printer does not report its heads' filament (unload is for the Snapmaker U1)")
    n = len(t["filament_exist"])
    heads = list(range(n)) if which == "all" else [int(which.upper().lstrip("T"))]
    for i in heads:
        if not 0 <= i < n:
            sys.exit(f"no head T{i}: this printer has T0 to T{n - 1}")
    todo = []
    for i in heads:
        name = f'T{i} {t["filament_type"][i]} {t["filament_sub_type"][i]}'.strip()
        if t["filament_soft"][i]:
            print(f"{name}: soft filament, unload it from the printer's screen")
        elif channel_state(i) != "load_finish":   # unload_finish and preload_finish (parked at the feeder) mean empty head
            print(f"T{i}: nothing loaded in the head ({channel_state(i)})")
        else:
            todo.append((i, name))
    if not todo:
        return

    def idle():
        return query("machine_state_manager")["machine_state_manager"]["main_state"] == 0

    def hot(i):
        h = query(head_obj(i))[head_obj(i)]
        return h["target"] > 0 and h["temperature"] >= h["target"] - 5

    call("/printer/gcode/script?script=" + urllib.parse.quote("AUTO_FEEDING_BATCH ACTION=START"), "POST")
    try:
        for k, (i, name) in enumerate(todo):
            nxt = f" NEXT_EXTRUDER={todo[k + 1][0]}" if k + 1 < len(todo) else ""
            print(f"{name}: heating...", flush=True)
            run_waiting(f"AUTO_FEEDING_BATCH ACTION=DOING STAGE=prepare EXTRUDER={i}{nxt} UNLOAD=1", lambda: hot(i), 240)
            print(f"{name}: unloading...", flush=True)
            run_waiting(f"AUTO_FEEDING_BATCH ACTION=DOING STAGE=doing EXTRUDER={i} UNLOAD=1",
                        lambda: idle() and channel_state(i) == "unload_finish", 300)
            print(f"{name}: unloaded")
    finally:
        call("/printer/gcode/script?script=" + urllib.parse.quote("AUTO_FEEDING_BATCH ACTION=STOP"), "POST")


def known_filaments():
    """{type: {vendor: [sub-types]}} from the U1's own filament table (config/snapmaker/filament_parameters.json)."""
    d = json.loads(call("/server/files/config/snapmaker/filament_parameters.json", raw=True))
    return {t: {v[len("vendor_"):]: [x[len("sub_"):] for x in subs] for v, subs in vendors.items() if v.startswith("vendor_")}
            for t, vendors in d.items() if isinstance(vendors, dict)}


def set_filament(head, ftype, vendor, sub, colour, force=False):
    """Snapmaker U1: SET_PRINT_FILAMENT_CONFIG, what the screen sends after you edit a head's filament. Names are matched to the
    printer's table (any case); an unknown vendor or sub-type falls back to the type's generic settings, as the firmware does."""
    if state() in RUNNING:
        sys.exit("refused: a job is running; change the filament after it ends")
    i = int(head.upper().lstrip("T"))
    known = known_filaments()
    t = next((k for k in known if k.lower() == ftype.lower()), None)
    if not t:
        sys.exit(f"the printer does not know type {ftype!r}; it knows: {', '.join(known)}")
    v = next((k for k in known[t] if k.lower() == vendor.lower()), None)
    v_note = "" if v else f" (vendor {vendor!r} not in its table: generic {t} settings)"
    v = v or vendor
    subs = known[t].get(v, []) + known[t].get("generic", [])
    sb = next((k for k in subs if k.lower() == sub.lower()), sub)
    hexc = colour.lstrip("#").upper()
    if len(hexc) != 6 or any(c not in "0123456789ABCDEF" for c in hexc):
        sys.exit("colour as six hex digits, e.g. 1f1f22")
    cmd = (f"SET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER={i} VENDOR={v} FILAMENT_TYPE={t} FILAMENT_SUBTYPE={sb} "
           f"FILAMENT_COLOR_RGBA={hexc}FF" + (" FORCE=1" if force else ""))
    import re
    if not all(re.fullmatch(r"[A-Za-z0-9_.+-]+", w) for w in (v, t, sb)):
        sys.exit("names with spaces or symbols need the printer's screen")   # one G-code line, plain words only
    try:
        call("/printer/gcode/script?script=" + urllib.parse.quote(cmd), "POST", timeout=60)
    except urllib.error.HTTPError as e:
        msg = json.loads(e.read() or b"{}").get("error", {}).get("message", str(e))
        if "official filament" in msg:
            sys.exit(f"T{i} holds a Snapmaker chipped spool, which sets itself from its tag; pass --force to override")
        sys.exit(msg)
    now = query("print_task_config")["print_task_config"]
    print(f'T{i}: {now["filament_vendor"][i]} {now["filament_type"][i]} {now["filament_sub_type"][i]} '
          f'#{now["filament_color_rgba"][i][:6]}{v_note}')


def watch(every, frames):
    os.makedirs(frames, exist_ok=True)
    last, seen_running = None, False
    while True:
        try:
            s = status()
        except OSError as e:
            print("unreachable:", e, flush=True)
            time.sleep(every)
            continue
        seen_running |= s["state"] in RUNNING
        print(time.strftime("%H:%M"), s["state"], s["layer"] or "", f'{s["progress_pct"]}%',
              f'{s.get("left_min", "?")} min left', flush=True)
        if s["layer"] != last:
            last = s["layer"]
            try:
                f = snap(os.path.join(frames, f'{time.strftime("%H%M%S")}-layer{(last or "0").split("/")[0]}.jpg'))
                print("frame:", f, flush=True)
            except OSError:
                pass
        if seen_running and s["state"] not in RUNNING:
            print("ended:", s["state"], s.get("message", ""))
            done = s["state"] == "complete"
            notify(f'{"Print finished" if done else "Print stopped"}: {s["file"] or "job"}',
                   f'{s["state"]} after {s["printing_min"]:.0f} min, {s["filament_used_m"]} m of filament. {s.get("message", "")}'.strip(),
                   "good" if done else "bad")
            return s["state"]
        time.sleep(every)


def main():
    global BASE, CAMERA
    conf = json.load(open(CONF, encoding="utf-8")) if os.path.exists(CONF) else {}
    CFG.update(conf)
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0], formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="\n".join(__doc__.splitlines()[2:]))
    ap.add_argument("--printer", default=os.environ.get("PRINTER_HOST") or conf.get("host"))
    ap.add_argument("--port", type=int, default=conf.get("port", 7125))
    ap.add_argument("--camera", default=conf.get("camera", "/server/files/camera/monitor.jpg"))
    ap.add_argument("cmd")
    ap.add_argument("args", nargs="*")
    ap.add_argument("--bed-clear", action="store_true")
    ap.add_argument("--queue", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--frames", default="printer-frames")
    ap.add_argument("--level", default="info", choices=list(LEVEL))
    ap.add_argument("--vendor", default="generic")
    ap.add_argument("--sub", default="generic")
    ap.add_argument("--colour", default="FFFFFF")
    a = ap.parse_args()
    if a.cmd == "notify":
        r = notify(a.args[0], " ".join(a.args[1:]), a.level)
        if r is None:
            sys.exit("no Discord webhook set (PRINTER_DISCORD_WEBHOOK or discord_webhook in printer.json)")
        print("posted" if r else "not posted")
        sys.exit(0 if r else 1)
    if not a.printer:
        sys.exit(f"no printer: pass --printer, set PRINTER_HOST, or write {{\"host\": \"...\"}} to {CONF}")
    BASE, CAMERA = f"http://{a.printer}:{a.port}", a.camera
    c, x = a.cmd, a.args

    def need_clear():
        if not a.bed_clear:
            sys.exit("refused: look at `snap` (or ask) and start only on an empty bed, then pass --bed-clear")
        if state() in RUNNING:
            sys.exit("refused: a job is already running; queue it instead")

    if c == "status":
        print(json.dumps(status(), indent=1))
    elif c == "snap":
        print(snap(x[0] if x else "printer-snap.jpg"))
    elif c == "files":
        for f in sorted(call("/server/files/list?root=gcodes"), key=lambda f: -f["modified"]):
            print(time.strftime("%Y-%m-%d %H:%M", time.localtime(f["modified"])), f'{f["size"] / 1e6:6.1f} MB', f["path"])
    elif c == "send":
        print("on the printer:", send(x[0], a.queue), "(queued)" if a.queue else "")
    elif c == "start":
        need_clear()
        print(call("/printer/print/start?filename=" + urllib.parse.quote(x[0]), "POST"))
    elif c == "queue":
        sub = x[0] if x else "list"
        if sub == "list":
            q = call("/server/job_queue/status")
            print("queue:", q["queue_state"])
            for j in q["queued_jobs"]:
                print(j["job_id"], j["filename"])
        elif sub == "add":
            if queue_running():
                sys.exit("refused: the queue is running and would start this onto the last print; `queue hold` first")
            print(call("/server/job_queue/job?filenames=" + urllib.parse.quote(x[1]), "POST"))
        elif sub == "remove":
            print(call("/server/job_queue/job?job_ids=" + urllib.parse.quote(x[1]), "DELETE"))
        elif sub == "start":
            need_clear()
            print(call("/server/job_queue/start", "POST"))
            call("/server/job_queue/pause", "POST")   # one job per bed check: hold the queue behind it
            print("started the next job; the queue is held again behind it")
        elif sub == "hold":
            print(call("/server/job_queue/pause", "POST"))
        else:
            sys.exit("queue: list, add FILE, remove ID, start --bed-clear, hold")
    elif c in ("pause", "resume", "cancel"):
        print(call(f"/printer/print/{c}", "POST"))
    elif c == "light":
        print(gcode(f"SET_LED LED={conf.get('light', 'cavity_led')} WHITE={1 if x and x[0] == 'on' else 0}", force=True))
    elif c == "unload":
        if not x:
            sys.exit("unload T0..T3, or unload all")
        unload(x[0])
    elif c == "filament":
        if x and x[0] == "list":
            for t, vendors in known_filaments().items():
                print(f"{t}: " + "; ".join(f"{v} ({', '.join(subs)})" for v, subs in vendors.items()))
        elif len(x) >= 2:
            set_filament(x[0], x[1], a.vendor, a.sub, a.colour, a.force)
        else:
            sys.exit("filament T0..T3 TYPE --colour RRGGBB [--vendor NAME] [--sub NAME], or filament list")
    elif c == "gcode":
        print(gcode(x[0], a.force))
    elif c == "watch":
        watch(int(x[0]) if x else 60, a.frames)
    elif c == "history":
        for j in call(f"/server/history/list?limit={int(x[0]) if x else 10}&order=desc")["jobs"]:
            print(time.strftime("%Y-%m-%d %H:%M", time.localtime(j["start_time"])), f'{j["status"]:<11}',
                  f'{j["print_duration"] / 60:5.0f} min', f'{j["filament_used"] / 1000:6.2f} m', j["filename"])
    else:
        sys.exit(f"unknown command {c!r}; run with --help")


if __name__ == "__main__":
    main()
