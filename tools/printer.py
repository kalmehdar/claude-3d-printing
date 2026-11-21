"""Drive a Klipper/Moonraker printer from the command line: what Claude uses to send, start, watch and stop prints.

    python printer.py status                      state, layer, progress, time left, temperatures, loaded filament
    python printer.py snap [out.jpg]              the camera's current frame (look at it before every start)
    python printer.py files                       G-code on the printer, newest first
    python printer.py send part.gcode [--queue]   upload (and add to the job queue); never starts anything
    python printer.py start part.gcode --bed-clear   start now; the flag says the bed was seen empty (camera or a person)
    python printer.py queue [add FILE | remove ID | start --bed-clear | hold]
    python printer.py pause | resume | cancel
    python printer.py light on|off                chamber light (Snapmaker U1: cavity_led)
    python printer.py gcode "CMD" [--force]       raw G-code; refused while a job runs unless --force
    python printer.py watch [secs] [--frames DIR] poll until the job ends, one camera frame per layer into DIR
    python printer.py history [n]

The printer: --printer ADDRESS, or PRINTER_HOST, or {"host": ...} in ~/.config/claude-3d-printing/printer.json.
The camera: --camera PATH on Moonraker (default /server/files/camera/monitor.jpg, the Snapmaker U1's still).
Moonraker often has no password: use it on a home network or over a VPN only. Standard library only.
"""
import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import uuid

CONF = os.path.join(os.path.expanduser("~"), ".config", "claude-3d-printing", "printer.json")
RUNNING = ("printing", "paused")
BASE = CAMERA = None


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


def snap(out):
    open(out, "wb").write(call(CAMERA, raw=True, timeout=15))
    return out


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
        call("/server/job_queue/job?filenames=" + urllib.parse.quote(name), "POST")
    return name


def gcode(cmd, force=False):
    if not force and state() in RUNNING:
        sys.exit("refused: a job is running; --force only for harmless commands")
    return call("/printer/gcode/script?script=" + urllib.parse.quote(cmd), "POST", timeout=300)


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
            return s["state"]
        time.sleep(every)


def main():
    global BASE, CAMERA
    conf = json.load(open(CONF, encoding="utf-8")) if os.path.exists(CONF) else {}
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
    a = ap.parse_args()
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
            print(call("/server/job_queue/job?filenames=" + urllib.parse.quote(x[1]), "POST"))
        elif sub == "remove":
            print(call("/server/job_queue/job?job_ids=" + urllib.parse.quote(x[1]), "DELETE"))
        elif sub == "start":
            need_clear()
            print(call("/server/job_queue/start", "POST"))
        elif sub == "hold":
            print(call("/server/job_queue/pause", "POST"))
        else:
            sys.exit("queue: list, add FILE, remove ID, start --bed-clear, hold")
    elif c in ("pause", "resume", "cancel"):
        print(call(f"/printer/print/{c}", "POST"))
    elif c == "light":
        print(gcode(f"SET_LED LED={conf.get('light', 'cavity_led')} WHITE={1 if x and x[0] == 'on' else 0}", force=True))
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
