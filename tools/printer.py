"""Drive a Klipper/Moonraker printer from the command line: what Claude uses to send, start, watch and stop prints.

    python printer.py status                      state, layer, progress, time left, temperatures, loaded filament
    python printer.py snap [out.jpg]              the camera's current frame (look at it before every start)
    python printer.py files                       G-code on the printer, newest first
    python printer.py send part.gcode [--queue]   upload (and add to the job queue); never starts anything
    python printer.py start part.gcode --bed-clear   start now; the flag says the bed was seen empty (camera or a person)
    python printer.py queue [add FILE | remove ID | start --bed-clear | hold]
    python printer.py pause | resume | cancel
    python printer.py speed [50-150]              the speed override (as on the screen), safe mid-print; no number shows it
    python printer.py speed [50-150]              the speed override (as on the screen), safe mid-print; no number shows it
    python printer.py light on|off                chamber light (Snapmaker U1: cavity_led)
    python printer.py unload T2 | all             Snapmaker U1: unload a head's filament back to the spool (its own feeder routine)
    python printer.py filament T2 PETG --vendor Polymaker --sub generic --colour 1f1f22
                                                  Snapmaker U1: tell the printer what was loaded on a head (as its screen does)
    python printer.py filament list               the types, vendors and sub-types the printer knows
    python printer.py gcode "CMD" [--force]       raw G-code; refused while a job runs unless --force
    python printer.py watch [secs] [--frames DIR] [--quiet]   poll until the job ends, one photo per layer; --quiet: no alerts
    python printer.py history [n]
    python printer.py timelapse [FOLDER]          a short video from a print's layer photos (default the newest print; needs ffmpeg)
    python printer.py log [FILE]                  add finished jobs not yet in FILE (a markdown print log; default "log_file" in printer.json)
    python printer.py notify "Title" ["text"] [--level good|warn|bad]   phone push (ntfy) and/or Discord, whichever is set up

The printer: --printer ADDRESS, or PRINTER_HOST, or {"host": ...} in ~/.config/claude-3d-printing/printer.json.
The camera: whatever Moonraker lists (stock U1: /server/files/camera/monitor.jpg; Extended Firmware: /webcam/snapshot.jpg), or --camera PATH|URL.
Phone push: "ntfy_topic" in printer.json (a long random name; install the ntfy app and subscribe to it). "ntfy_server" if self-hosted.
Discord: a channel webhook URL in PRINTER_DISCORD_WEBHOOK or "discord_webhook" in printer.json (keep it out of git);
"discord_mention" (e.g. <@123>) is added to warn and bad posts.
Moonraker often has no password: use it on a home network or over a VPN only. Standard library only.
"""
import argparse
import base64
import json
import os
import subprocess
import shutil
import tempfile
import re
import socket
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

CONF = os.path.join(os.path.expanduser("~"), ".config", "claude-3d-printing", "printer.json")
FRAMES = os.path.join(os.path.dirname(CONF), "frames")   # watch keeps one folder of layer photos per job here; Workshop shows them
ACTIVITY = os.path.join(os.path.dirname(CONF), "activity.jsonl")   # who did what, shared with Workshop
RUNNING = ("printing", "paused")
BASE = CAMERA = None
CFG = {}
LEVEL = {"info": 1920728, "good": 955268, "warn": 11880200, "bad": 11805464}


def load_conf():
    """printer.json into CFG (once); Workshop calls this too so both send the same alerts."""
    if not CFG and os.path.exists(CONF):
        CFG.update(json.load(open(CONF, encoding="utf-8")))
    return CFG


def push(title, text="", level="info", click=None):
    """Phone push through ntfy if "ntfy_topic" is set in printer.json: True sent, False failed, None not set up.
    The topic name is the only key: anyone who knows it can read the alerts, so keep it long and out of git."""
    topic = load_conf().get("ntfy_topic")
    if not topic:
        return None
    body = {"topic": topic, "title": title[:250], "message": text[:3000] or title, "priority": 4 if level in ("warn", "bad") else 3,
            "tags": [{"good": "white_check_mark", "warn": "warning", "bad": "x"}.get(level, "printer")]}
    if click:
        body["click"] = click
    req = urllib.request.Request(CFG.get("ntfy_server", "https://ntfy.sh"), data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "claude-3d-printing"})
    try:
        urllib.request.urlopen(req, timeout=10).read()
        return True
    except OSError as e:
        print("push failed:", getattr(e, "code", e), file=sys.stderr)   # never echo the topic
        return False


def notify(title, text="", level="info", click=None):
    """Phone push (ntfy) and a Discord embed, whichever are set up: True if any sent, False if all failed, None if none set up."""
    sent = [push(title, text, level, click), discord(title, text, level)]
    return None if all(r is None for r in sent) else any(sent)


def discord(title, text="", level="info"):
    """Post a Discord embed if a webhook is set up: True posted, False failed, None when no webhook is set."""
    url = os.environ.get("PRINTER_DISCORD_WEBHOOK") or load_conf().get("discord_webhook")
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


def record(what, detail="", who=None, via="printer.py"):
    """Append one line to the shared activity log: who did what to the printer, from where."""
    import getpass
    who = who or os.environ.get("PRINTER_ACTOR") or CFG.get("actor") or getpass.getuser()
    try:
        os.makedirs(os.path.dirname(ACTIVITY), exist_ok=True)
        with open(ACTIVITY, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.time(), "who": who[:40], "what": what, "detail": str(detail)[:300], "via": via}) + "\n")
    except OSError:
        pass


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


STOCK_STILL = "/server/files/camera/monitor.jpg"   # Snapmaker U1 stock firmware: a file Moonraker serves
_cam = {"url": None, "t": 0.0}


def camera_url():
    """Full URL of the camera still. --camera (or "camera" in printer.json) wins; otherwise the snapshot Moonraker lists,
    so the same tools follow the printer from stock (its monitor.jpg on Moonraker) to the Extended Firmware's 25-30 fps
    camera (/webcam/snapshot.jpg on port 80) with no change here."""
    path = CAMERA
    if not path:
        if not _cam["url"] or time.time() - _cam["t"] > 60:
            try:
                cams = [c for c in call("/server/webcams/list", timeout=5).get("webcams", []) if c.get("enabled", True)]
                snaps = [c["snapshot_url"] for c in cams if c.get("snapshot_url")]
                # a real camera service beats the stock still, which may survive in Moonraker's database after a flash
                _cam["url"] = next((u for u in snaps if not u.endswith(STOCK_STILL)), None) or (snaps or [STOCK_STILL])[0]
            except (OSError, ValueError, KeyError):
                _cam["url"] = STOCK_STILL
            _cam["t"] = time.time()
        path = _cam["url"]
    if path.startswith("http"):
        return path
    if path.startswith("/server/"):
        return BASE + path
    return "http://" + BASE[len("http://"):].rsplit(":", 1)[0] + path   # the printer's web server, not Moonraker's port


def camera_age():
    """Seconds since the stock camera still was written, or None when the printer does not list a camera folder."""
    try:
        files = call("/server/files/list?root=camera", timeout=5)
    except OSError:
        return None
    hit = next((f for f in files if camera_url().endswith("/" + f["path"])), None)
    return time.time() - hit["modified"] if hit else None


def wake_camera():
    """Snapmaker U1: the still only refreshes while something 'watches' (its app sends camera.start_monitor).
    Ask for it when the still is stale, then wait for a fresh frame. Harmless on printers without it."""
    if not camera_url().endswith(STOCK_STILL):   # a real camera service streams by itself
        return
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
    with urllib.request.urlopen(camera_url(), timeout=15) as r:
        data = r.read()
    open(out + ".part", "wb").write(data)
    os.replace(out + ".part", out)   # a reader never sees half a JPEG
    return out


def run_folder(job_file):
    """Photo folder for the print now running: <file name>-<Moonraker history job id>, so a reprint gets its own folder."""
    stem = re.sub(r"[^A-Za-z0-9_.-]", "_", os.path.splitext(os.path.basename(job_file or "job"))[0]).lstrip(".") or "job"   # URL- and path-safe
    try:
        j = call("/server/history/list?limit=1&order=desc")["jobs"][0]
        if j["filename"] == job_file and j["status"] == "in_progress":
            return f'{stem}-{j["job_id"]}'
    except (OSError, KeyError, IndexError, ValueError):
        pass
    return stem


def layer_photo(job_file, layer, frames=None):
    """One photo per layer per print run, shared by watch and Workshop: returns the existing photo for this layer, or takes one."""
    d = os.path.join(frames or FRAMES, run_folder(job_file))
    os.makedirs(d, exist_ok=True)
    tag = f"-layer{layer}.jpg"
    have = sorted(f for f in os.listdir(d) if f.endswith(tag))
    if have:
        return os.path.join(d, have[-1])
    return snap(os.path.join(d, time.strftime("%Y%m%d-%H%M%S") + tag))


def timelapse(folder, seconds=12):
    """Turn a print run's layer photos into folder/timelapse.mp4 (about `seconds` long) with ffmpeg. Returns the path."""
    pics = sorted(f for f in os.listdir(folder) if f.endswith(".jpg"))
    if len(pics) < 10:
        raise ValueError(f"only {len(pics)} photos in {folder}; a timelapse needs 10 or more")
    import glob
    winget = glob.glob(os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Packages", "*FFmpeg*", "*", "bin", "ffmpeg.exe"))
    ff = os.environ.get("FFMPEG") or shutil.which("ffmpeg") or next(
        (p for p in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", *sorted(winget)) if os.path.exists(p)), None)
    if not ff:
        raise OSError("ffmpeg is not installed (Mac: brew install ffmpeg; Windows: winget install Gyan.FFmpeg; or set FFMPEG)")
    fps = max(8, min(30, round(len(pics) / seconds)))
    out = os.path.join(folder, "timelapse.mp4")
    fd, lst = tempfile.mkstemp(prefix=".frames-", suffix=".txt", dir=folder)
    part = lst[:-4] + ".mp4"
    with os.fdopen(fd, "w") as f:   # concat list, not a shell glob: file names never reach a shell
        f.writelines(f"file '{p}'\nduration {1 / fps:.4f}\n" for p in pics)
    try:
        subprocess.run([ff, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst, "-vf", "scale=1280:-2,format=yuv420p",
                        "-r", str(fps), "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-movflags", "+faststart", part],
                       check=True, timeout=600, cwd=folder)
        os.replace(part, out)
    finally:
        for f in (lst, part):
            if os.path.exists(f):
                os.remove(f)
    return out


def prune_photos(days=30, frames=None):
    """Delete print-run photo folders not touched for `days` days (Khalid, 21 Nov 2026: keep 30 days)."""
    root, gone = frames or FRAMES, []
    cut = time.time() - days * 86400
    for d in os.listdir(root) if os.path.isdir(root) else []:
        p = os.path.join(root, d)
        try:
            if os.path.isdir(p) and not os.path.islink(p) and os.path.getmtime(p) < cut:
                shutil.rmtree(p)
                gone.append(d)
        except OSError:
            pass   # one stuck folder does not stop the rest
    return gone


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


def heads_not_loaded(filename):
    """Snapmaker U1: heads the file uses whose feeder does not report load_finish (filament only parked at the feeder
    runs out at the first move). Empty on printers without feeders."""
    import re
    text = call("/server/files/gcodes/" + urllib.parse.quote(filename), raw=True, timeout=120).decode(errors="replace")
    heads = sorted({int(t) for t in re.findall(r"^T([0-3])\s*$", text, re.M)}) or [0]
    problems = [f"T{i} not loaded into the head ({channel_state(i)})" for i in heads if channel_state(i) not in (None, "load_finish")]
    bed = re.search(r"^M1[49]0 S(\d+)", text, re.M)   # the first bed command, wait or not (the start G-code's)
    if bed and bed.group(1) == "0":   # a plate type the filament has no temperature for: it would print on a cold bed
        problems.append("the file heats the bed to 0 C (re-slice for the real plate)")
    return problems


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
    return (f'T{i}: {now["filament_vendor"][i]} {now["filament_type"][i]} {now["filament_sub_type"][i]} '
            f'#{now["filament_color_rgba"][i][:6]}{v_note}')


def log_jobs(path, limit=50):
    """Append finished jobs to a markdown print log, oldest first, skipping any already there (matched by start time + file)."""
    jobs = [j for j in call(f"/server/history/list?limit={limit}&order=asc")["jobs"] if j["status"] != "in_progress"]
    text = open(path, encoding="utf-8").read() if os.path.exists(path) else (
        "# Print log\n\n| Date | File | Filament | Slicer time / g | Printer time | Outcome | Notes |\n"
        "| --- | --- | --- | --- | --- | --- | --- |\n")
    added = []
    for j in jobs:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(j["start_time"]))
        if f"| {when} | {j['filename']} |" in text:
            continue
        m = j.get("metadata") or {}
        est = m.get("estimated_time")
        slicer = (f"{est // 3600:.0f} h {est % 3600 / 60:02.0f} m" if est else "?") + (
            f" / {m['filament_weight_total']:.1f} g" if m.get("filament_weight_total") else "")
        fil = m.get("filament_type") or m.get("filament_name") or ""
        row = (f"| {when} | {j['filename']} | {fil} | {slicer} | {j['print_duration'] / 60:.0f} min, "
               f"{j['filament_used'] / 1000:.2f} m | {j['status']} | |\n")
        text += row
        added.append(row.strip())
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return added


def bed_off_midprint(s):
    """Printing above the first layer with the bed heater off, not on the last layer (the U1's PRINT_END turns heaters off
    while the job still reads as printing)."""
    cur, _, tot = (s.get("layer") or "0/0").partition("/")
    return (s.get("state") == "printing" and str(s.get("bed", "")).endswith("/0") and cur.isdigit() and tot.isdigit()
            and 0 < int(cur) < int(tot))


def watch(every, frames, alerts=True):
    os.makedirs(frames, exist_ok=True)
    last, seen_running, cold_n = None, False, 0
    while True:
        try:
            s = status()
        except OSError as e:
            print("unreachable:", e, flush=True)
            time.sleep(every)
            continue
        seen_running |= s["state"] in RUNNING
        cold_n = cold_n + 1 if bed_off_midprint(s) else 0
        if cold_n == 2:   # two readings in a row, not one blip
            # seen 21 Nov 2026: resuming a U1 job after a runout left the bed heater off; a cold first layer comes loose
            call("/printer/print/pause", "POST")
            record("paused (bed heater off)", s["file"] or "", who="watch")
            print("PAUSED: printing with the bed heater off (" + s["bed"] + " C); check before resuming", flush=True)
            alerts and notify(f'Print paused: {s["file"]}', f'The bed heater was off ({s["bed"]} C) while printing. Check the bed, then restart.', "warn")
        print(time.strftime("%H:%M"), s["state"], s["layer"] or "", f'{s["progress_pct"]}%',
              f'{s.get("left_min", "?")} min left', flush=True)
        if s["layer"] != last:
            last = s["layer"]
            try:
                f = layer_photo(s["file"], (last or "0").split("/")[0], frames)
                print("frame:", f, flush=True)
            except OSError:
                pass
        if seen_running and s["state"] not in RUNNING:
            print("ended:", s["state"], s.get("message", ""))
            done = s["state"] == "complete"
            alerts and notify(f'{"Print finished" if done else "Print stopped"}: {s["file"] or "job"}',
                   f'{s["state"]} after {s["printing_min"]:.0f} min, {s["filament_used_m"]} m of filament. {s.get("message", "")}'.strip(),
                   "good" if done else "bad")
            if CFG.get("log_file"):
                try:
                    for row in log_jobs(os.path.expanduser(CFG["log_file"])):
                        print("logged:", row, flush=True)
                except OSError as e:
                    print("could not log:", e, flush=True)
            return s["state"]
        time.sleep(every)


def main():
    global BASE, CAMERA
    conf = load_conf()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0], formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="\n".join(__doc__.splitlines()[2:]))
    ap.add_argument("--printer", default=os.environ.get("PRINTER_HOST") or conf.get("host"))
    ap.add_argument("--port", type=int, default=conf.get("port", 7125))
    ap.add_argument("--camera", default=conf.get("camera"), help="camera still path or URL (default: what Moonraker lists)")
    ap.add_argument("cmd")
    ap.add_argument("args", nargs="*")
    ap.add_argument("--bed-clear", action="store_true")
    ap.add_argument("--queue", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--frames", default=FRAMES)
    ap.add_argument("--quiet", action="store_true", help="watch: no phone or Discord alerts (Workshop already sends them)")
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

    def need_loaded(filename):
        missing = heads_not_loaded(filename)
        if missing:
            sys.exit("refused: " + "; ".join(missing))

    if c == "status":
        print(json.dumps(status(), indent=1))
    elif c == "snap":
        print(snap(x[0] if x else "printer-snap.jpg"))
    elif c == "files":
        for f in sorted(call("/server/files/list?root=gcodes"), key=lambda f: -f["modified"]):
            print(time.strftime("%Y-%m-%d %H:%M", time.localtime(f["modified"])), f'{f["size"] / 1e6:6.1f} MB', f["path"])
    elif c == "send":
        print("on the printer:", send(x[0], a.queue), "(queued)" if a.queue else "")
        record("sent" + (" and queued" if a.queue else ""), os.path.basename(x[0]))
    elif c == "start":
        need_clear()
        need_loaded(x[0])
        print(call("/printer/print/start?filename=" + urllib.parse.quote(x[0]), "POST"))
        record("started", x[0])
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
            jobs = call("/server/job_queue/status").get("queued_jobs") or []
            if jobs:
                need_loaded(jobs[0]["filename"])
            print(call("/server/job_queue/start", "POST"))
            call("/server/job_queue/pause", "POST")   # one job per bed check: hold the queue behind it
            print("started the next job; the queue is held again behind it")
        elif sub == "hold":
            print(call("/server/job_queue/pause", "POST"))
        else:
            sys.exit("queue: list, add FILE, remove ID, start --bed-clear, hold")
    elif c in ("pause", "resume", "cancel"):
        print(call(f"/printer/print/{c}", "POST"))
        record({"pause": "paused", "resume": "resumed", "cancel": "cancelled"}[c], status().get("file") or "")
    elif c == "speed":   # the screen's speed override (M220): safe mid-print, bounded so a typo can't wreck a job
        if not x:
            q = query("gcode_move")["gcode_move"]
            print(f'speed {q["speed_factor"] * 100:.0f}%')
        else:
            pct = int(x[0].rstrip("%"))
            if not 50 <= pct <= 150:
                sys.exit("refused: speed is 50 to 150 percent")
            call("/printer/gcode/script?script=" + urllib.parse.quote(f"M220 S{pct}"), "POST")
            record(f"speed set to {pct}%", status().get("file") or "")
            print(f"speed {pct}%")
    elif c == "light":
        print(gcode(f"SET_LED LED={conf.get('light', 'cavity_led')} WHITE={1 if x and x[0] == 'on' else 0}", force=True))
    elif c == "unload":
        if not x:
            sys.exit("unload T0..T3, or unload all")
        record("unload requested", x[0])
        unload(x[0])
    elif c == "filament":
        if x and x[0] == "list":
            for t, vendors in known_filaments().items():
                print(f"{t}: " + "; ".join(f"{v} ({', '.join(subs)})" for v, subs in vendors.items()))
        elif len(x) >= 2:
            r = set_filament(x[0], x[1], a.vendor, a.sub, a.colour, a.force)
            print(r)
            record("set filament", r)
        else:
            sys.exit("filament T0..T3 TYPE --colour RRGGBB [--vendor NAME] [--sub NAME], or filament list")
    elif c == "gcode":
        print(gcode(x[0], a.force))
    elif c == "watch":
        watch(int(x[0]) if x else 60, a.frames, not a.quiet)
    elif c == "log":
        path = os.path.expanduser(x[0] if x else conf.get("log_file", ""))
        if not path:
            sys.exit('give the log file, or set "log_file" in printer.json')
        added = log_jobs(path)
        print("\n".join(added) if added else "nothing new to log")
    elif c == "timelapse":
        runs = sorted((os.path.join(FRAMES, d) for d in os.listdir(FRAMES)), key=os.path.getmtime) if os.path.isdir(FRAMES) else []
        print(timelapse(os.path.expanduser(x[0]) if x else (runs or [None])[-1] or sys.exit("no print photos yet")))
    elif c == "history":
        for j in call(f"/server/history/list?limit={int(x[0]) if x else 10}&order=desc")["jobs"]:
            print(time.strftime("%Y-%m-%d %H:%M", time.localtime(j["start_time"])), f'{j["status"]:<11}',
                  f'{j["print_duration"] / 60:5.0f} min', f'{j["filament_used"] / 1000:6.2f} m', j["filename"])
    else:
        sys.exit(f"unknown command {c!r}; run with --help")


if __name__ == "__main__":
    main()
