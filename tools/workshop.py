"""Workshop: one page to watch, queue, preview and print on a Klipper/Moonraker printer, with the designs from this repo.

    python tools/workshop.py --printer 192.168.1.50                 serves http://<this machine>:8090
    python tools/workshop.py --printer 192.168.1.50 --bench examples/bench.json --bench ~/my-designs/bench.json --profile u1

What it serves:
    /                     the Workshop page (tools/workshop.html)
    /moonraker/<path>     the printer's Moonraker API, passed through (so the page works from a phone, and later over a VPN)
    /cam.jpg              the printer's camera still (Snapmaker U1 stock firmware: /server/files/camera/monitor.jpg)
    /bench/<n>/<path>     the designs' STLs and sources from bench n, read only
    /api/designs          every bench's designs with file links and real slicer numbers for --profile
    /api/slice  (POST)    {"bench": n, "id": design, "preset": "standard", "set": ["infill=30"], "upload": true, "queue": false}
                          packs the design's colour parts into a 3MF, slices it with tools/orca.py and uploads the G-code

Moonraker on many printers has no password. Run this only on a home network or behind a VPN such as Tailscale,
never on an open port. Standard library only; the page loads three.js from a CDN.
"""
import argparse
import ipaddress
import shutil
import http.server
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

TOOLS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(TOOLS)
sys.path.insert(0, TOOLS)

CFG = {}
SERVE = {".stl", ".scad", ".3mf", ".glb", ".json", ".png", ".jpg"}   # what /bench may hand out: design files only
SLICE_SETS = {"infill": r"\d{1,3}", "walls": r"\d{1,2}", "supports": r"on|off", "brim": r"auto_brim|outer_only|no_brim"}
SKIP = {"host", "connection", "transfer-encoding", "content-length", "keep-alive", "content-encoding"}


def root_of(bench_path):
    """Files a bench may serve: the whole repo for a bench inside it (examples point at ../hardware), else its own folder."""
    return REPO if bench_path.startswith(REPO + os.sep) else os.path.dirname(bench_path)


def bench_title(path):
    if path.startswith(REPO + os.sep):
        return "claude-3d-printing examples"
    return json.load(open(path, encoding="utf-8")).get("name") or os.path.basename(os.path.dirname(path))


def designs():
    out, label = [], CFG["profile"]
    for n, bench_path in enumerate(CFG["benches"]):
        bench = json.load(open(bench_path, encoding="utf-8"))
        base, root = os.path.dirname(bench_path), root_of(bench_path)
        stats_path = os.path.join(base, "slice-stats.json")
        stats = json.load(open(stats_path, encoding="utf-8")).get(CFG["profile"], {}) if os.path.exists(stats_path) else {}
        label = stats.get("label", label)
        out += bench_designs(n, bench, base, root, stats)
    return {"benches": [bench_title(b) for b in CFG["benches"]],
            "bed": CFG["bed"], "printer_label": label, "designs": out}


def bench_designs(n, bench, base, root, stats):
    def link(p):
        full = os.path.normpath(os.path.join(base, p))
        return f"/bench/{n}/" + os.path.relpath(full, root).replace(os.sep, "/") if full.startswith(root + os.sep) else None

    out = []
    for m in bench["models"]:
        parts = [{"name": e[0], "stl": link(e[1]), "colour": e[2],
                  "printed": not (len(e) > 3 and e[3] == "not printed")} for e in m["parts"]]
        out.append({"bench": n, "id": m["id"], "name": m["name"], "for": m.get("for", ""), "material": m.get("material", ""),
                    "status": m.get("status", ""), "state": m.get("state", ""),
                    "source": link(m["source"]) if m.get("source") else None, "parts": parts,
                    "slice": stats.get("designs", {}).get(m["id"])})
    return out


def safe_name(s):
    return re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-") or "print"


def upload_gcode(path, name):
    boundary = "workshop" + str(int(time.time() * 1000))
    data = open(path, "rb").read()
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(CFG["moonraker"] + "/server/files/upload", data=body, method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)


def preset_bad(p):
    import orca
    return p is not None and p not in orca.PRESETS


def slice_design(req):
    """Returns (http status, dict). Never raises for a slicer problem: the page shows the message."""
    import make_3mf
    import orca
    try:
        bench_path = CFG["benches"][int(req.get("bench", 0))]
    except (ValueError, IndexError, TypeError):
        return 404, {"error": "no such bench"}
    d = next((m for m in json.load(open(bench_path, encoding="utf-8"))["models"] if m["id"] == req.get("id")), None)
    if not d:
        return 404, {"error": f"no design {req.get('id')!r}"}
    base = os.path.dirname(bench_path)
    parts = [(e[0], os.path.join(base, e[1]), e[2]) for e in d["parts"] if not (len(e) > 3 and e[3] == "not printed")]
    if not parts:
        return 400, {"error": "this design has nothing to print"}
    heads, material = loaded_heads(), ""
    head_note = None
    if heads:   # a tool changer that reports its filament: put each colour on the head holding it
        mapping, missing = make_3mf.match_heads([c for _, _, c in parts], [h["colour"] for h in heads])
        if missing and not req.get("allow_substitute"):
            return 409, {"error": "Not loaded on any head: " + ", ".join(missing) + ". Load it, or slice anyway with the nearest colour.",
                         "missing": missing, "heads": heads}
        material = ",".join(h["material"] for h in heads)
        head_note = [{"part": label, "colour": c, "head": f"T{mapping[c.lower()]}", "loaded": heads[mapping[c.lower()]]["label"]}
                     for label, _, c in parts]
    preset = req.get("preset") or "standard"
    # only the plain settings the page offers: anything else could reach OrcaSlicer keys such as post_process
    sets = [s for s in (req.get("set") or []) if isinstance(s, str) and "=" in s
            and s.split("=", 1)[0] in SLICE_SETS and re.fullmatch(SLICE_SETS[s.split("=", 1)[0]], s.split("=", 1)[1])]
    if preset_bad(req.get("preset")):
        return 400, {"error": "unknown preset"}
    out = tempfile.mkdtemp(prefix="workshop-")
    try:
        return _slice_into(out, d, parts, heads, material, head_note, preset, sets, req, make_3mf, orca)
    finally:
        shutil.rmtree(out, ignore_errors=True)


def _slice_into(out, d, parts, heads, material, head_note, preset, sets, req, make_3mf, orca):
    try:
        data, _ = make_3mf.build(parts, d["name"], CFG["bed"], heads=[h["colour"] for h in heads] if heads else None)
        p3 = os.path.join(out, safe_name(d["id"]) + ".3mf")
        open(p3, "wb").write(data)
        s = orca.slice_([p3], CFG["profile"], preset=preset, material=material, sets=sets, out=out)
    except SystemExit as e:   # orca.py and make_3mf stop with a plain message
        return 422, {"error": str(e.code), "hint": "Slicing needs OrcaSlicer (ORCA_CLI) and a printer setup "
                     f"named {CFG['profile']!r} (see tools/orca.py)."}
    if s.get("error") or not s.get("gcode"):
        return 422, {"error": s.get("error", "no G-code")}
    name = f"{safe_name(d['id'])}-{preset}.gcode"
    result = {"time": s["time"], "grams": s["grams"], "total_g": s["total_g"], "changes": s["changes"],
              "preset": preset, "settings": s["settings"], "file": name, "heads": head_note}
    if req.get("upload", True):
        upload_gcode(s["gcode"], name)
        result["uploaded"] = True
        if req.get("queue"):
            with moonraker("GET", "/server/job_queue/status", timeout=5) as r:
                running = json.load(r)["result"]["queue_state"] in ("ready", "loading")
            if running:   # a running queue would start it onto the last print
                result["queue_note"] = "Not queued: the queue is running. Hold it, then add."
            else:
                moonraker("POST", "/server/job_queue/job?filenames=" + urllib.request.quote(name)).read()
                result["queued"] = True
    return 200, result


def loaded_heads():
    """[{colour, material, label}] per head when the printer reports its filament (Snapmaker U1), else None.
    material is matched against the slicer setup's filament names (pla-snapspeed, petg-hf...)."""
    try:
        with moonraker("GET", "/printer/objects/query?print_task_config", timeout=5) as r:
            t = json.load(r)["result"]["status"].get("print_task_config")
    except (OSError, ValueError, KeyError):
        return None
    if not t or not t.get("filament_color_rgba"):
        return None
    import orca
    try:
        have = list(orca.materials(orca.printer_dir(CFG["profile"])))
    except SystemExit:
        have = []
    heads = []
    exist = t.get("filament_exist") or [True] * len(t["filament_color_rgba"])
    for ty, sub, c, here in zip(t.get("filament_type", []), t.get("filament_sub_type", []), t["filament_color_rgba"], exist):
        if not here:   # an empty head still reports its last colour: never send a part to it
            heads.append({"colour": None, "material": have[0] if have else "", "label": "empty"})
            continue
        exact = f"{ty}-{sub}".lower().replace(" ", "-") if sub and sub.lower() != "generic" else ty.lower()
        words = [w.lower().replace(" ", "-") for w in (sub, ty) if w and w.lower() != "generic"]
        pick = (exact if exact in have else None) or next((n for w in words for n in have if w in n.split("-")), None) or next(
            (n for n in have if ty.lower() in n), have[0] if have else ty.lower())   # PETG generic -> petg, not petg-hf
        heads.append({"colour": "#" + c[:6].lower(), "material": pick, "label": f"{ty} {sub} #{c[:6]}".strip()})
    return heads


ADVICE = [   # printer messages seen on the U1, and what the person should do about them
    (r"filament runout", "A head ran out of filament, or its new spool is only parked at the feeder. Load it from the printer's screen, then cancel and restart the print."),
    (r"state mismatch|unload_fail|load_fail", "A feeder is in an unexpected state. Check that head on the printer's screen."),
    (r"Probe samples exceed tolerance", "The bed probe got uneven readings, often from something on the plate. Check the bed is clear."),
    (r"auto feeding batch has not yet begun", "A feed step was sent outside a batch; harmless."),
    (r"heater .* not heating|thermal runaway|Heater .* not heating", "A heater fault: switch the printer off and check it before printing again."),
]


def actor(handler):
    who = re.sub(r"[^\w .'-]", "", handler.headers.get("X-Workshop-User") or "")[:24].strip()
    return who or "someone"


def record(what, detail="", who="someone", via="Workshop"):
    import printer
    printer.record(what, detail, who=who, via=via)


ALERT_LOG = os.path.join(os.path.expanduser("~"), ".config", "claude-3d-printing", "alerts.jsonl")
_alerts = []        # every warning seen, oldest first; the printer's own console only keeps a few minutes


def scan_alerts():
    """Copy new warnings and errors from the printer's console into the alert history (kept on disk)."""
    with moonraker("GET", "/server/gcode_store?count=1000", timeout=10) as r:
        store = json.load(r)["result"]["gcode_store"]
    seen = {(int(a["t"] // 60), a["text"]) for a in _alerts}   # the same message within a minute counts once
    for g in store:
        m = g["message"]
        if g["type"] != "response" or not (m.startswith("!!") or "// !!" in m or m.lower().startswith("error")):
            continue
        if re.search(r"(park|pick) extruder\d* !!!", m):   # the U1's tool-change chatter, not a warning
            continue
        text = re.sub(r"^(// )?!! ?", "", m).replace("raw msg:", " ").strip()[:200]
        if (int(g["time"] // 60), text) in seen:
            continue
        a = {"t": g["time"], "text": text, "advice": next((adv for pat, adv in ADVICE if re.search(pat, m, re.I)), "")}
        _alerts.append(a)
        seen.add((int(a["t"] // 60), text))
        try:
            with open(ALERT_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(a) + "\n")
        except OSError:
            pass


def alerts(limit=12):
    try:
        scan_alerts()
    except (OSError, ValueError, KeyError):
        pass
    return _alerts[::-1][:limit]


def watch_alerts():
    try:
        _alerts.extend(json.loads(x) for x in open(ALERT_LOG, encoding="utf-8").read().splitlines()[-200:] if x.strip())
    except OSError:
        pass
    while True:
        try:
            scan_alerts()
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(20)


CLAUDE_JOBS = []   # [{id, who, text, state, output, t}], newest last; kept in memory while Workshop runs
CLAUDE_TOOLS = ["Bash(python3 tools/printer.py *)", "Bash(python3 tools/orca.py *)", "Bash(python3 tools/make_3mf.py *)",
                "Bash(python3 tools/look.py *)", "Read"]


def claude_prompt(who, text, bed_clear):
    return f"""You are driving a Snapmaker U1 3D printer for {who}, from the Workshop page. Their request:

{text}

{"They ticked: the bed is empty." if bed_clear else "They did NOT say the bed is empty."}

Rules (from skills/print-workflow step 7 and docs/snapmaker-u1.md in this folder; read them if unsure):
- Use only python3 tools/printer.py, tools/orca.py, tools/make_3mf.py and tools/look.py from this folder. Designs are in the benches Workshop serves.
- Start a print only when they ticked that the bed is empty AND a fresh `python3 tools/printer.py snap bed.jpg` (read the image) shows an empty plate. Otherwise prepare everything, say so, and do not start.
- Never use `gcode --force`, and never send motion, heating or feeding commands except through printer.py's own start, pause, resume, cancel, unload and filament commands for what they asked.
- If anything is unclear or unsafe, do not act: say what you need from them.
Reply in two to five short plain sentences: what you did, what the printer is doing now, and anything they must do."""


def run_claude(job, bed_clear):
    import subprocess
    exe = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
    env = dict(os.environ, PRINTER_ACTOR=f"Claude for {job['who']}", PRINTER_HOST=CFG["printer"])
    try:
        r = subprocess.run([exe, "-p", claude_prompt(job["who"], job["text"], bed_clear), "--output-format", "json",
                            "--max-turns", "30", "--allowedTools", *CLAUDE_TOOLS],
                           cwd=REPO, env=env, capture_output=True, text=True, timeout=1500)
        d = json.loads(r.stdout or "{}")
        job["output"] = d.get("result") or (r.stderr or "no reply")[-600:]
        job["state"] = "error" if d.get("is_error") or r.returncode else "done"
    except (OSError, ValueError, subprocess.TimeoutExpired) as e:
        job["output"], job["state"] = f"Claude could not run: {e}", "error"
    record("Claude replied", job["output"][:200], who=f"Claude for {job['who']}")


_unloading = []


def start_unload(head):
    """Run printer.unload (the U1 screen's own batch protocol) in a thread; it takes about a minute per head."""
    import threading
    import printer
    printer.BASE, printer.CAMERA = CFG["moonraker"], CFG["camera"]
    if _unloading and _unloading[0].is_alive():
        return

    def go():
        try:
            printer.unload(head if head.lower() == "all" else "T" + head.upper().lstrip("T"))
        except SystemExit as e:
            sys.stderr.write(f"unload {head}: {e}\n")

    t = threading.Thread(target=go, daemon=True)
    _unloading[:] = [t]
    t.start()


_last_wake = [0.0]


def wake_camera():
    """At most every 5 s: if the camera still is stale, ask the printer to refresh it (printer.wake_camera)."""
    if time.time() - _last_wake[0] < 5:
        return
    _last_wake[0] = time.time()
    import printer
    printer.BASE, printer.CAMERA = CFG["moonraker"], CFG["camera"]
    try:
        printer.wake_camera()
    except OSError:
        pass


def moonraker(method, path, body=None, headers=None, timeout=30):
    req = urllib.request.Request(CFG["moonraker"] + path, data=body, method=method, headers=headers or {})
    return urllib.request.urlopen(req, timeout=timeout)


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if CFG.get("verbose"):
            sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def send(self, code, body, ctype="application/json", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else None

    def proxy(self, path, timeout=30):
        hdrs = {k: v for k, v in self.headers.items() if k.lower() not in SKIP}
        try:
            r = moonraker(self.command, path, self.body(), hdrs, timeout)
        except urllib.error.HTTPError as e:
            r = e
        except OSError as e:
            return self.send(502, {"error": f"printer unreachable: {e}"})
        data = r.read()
        self.send(r.status, data, r.headers.get("Content-Type", "application/octet-stream"))

    def allowed(self):
        """Refuse requests another web page could make: the Host must be an IP address, localhost or an --allow-host name
        (stops DNS rebinding), and anything but GET must carry the page's X-Workshop header and a same-origin Origin
        (a custom header forces a CORS preflight, which this server never grants)."""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        try:
            ipaddress.ip_address(host)
            host_ok = True
        except ValueError:
            host_ok = host in ("localhost", *CFG["allow_hosts"])
        if not host_ok:
            return False
        if self.command != "GET":
            origin = self.headers.get("Origin")
            if self.headers.get("X-Workshop") != "1" or (origin and origin != "http://" + self.headers.get("Host", "")):
                return False
        return True

    def printer_busy(self):
        try:
            with moonraker("GET", "/printer/objects/query?print_stats=state", timeout=5) as r:
                return json.load(r)["result"]["status"]["print_stats"]["state"] in ("printing", "paused")
        except (OSError, ValueError, KeyError):
            return True   # unknown counts as busy: refuse rather than guess

    def describe(self, path):
        """(what, detail) for the activity log when a request changes something on the printer, else None."""
        q = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)
        one = lambda k: (q.get(k) or [""])[0]
        if self.command == "POST":
            for key, what in (("/printer/print/start", "started"), ("/printer/print/pause", "paused"), ("/printer/print/resume", "resumed"),
                              ("/printer/print/cancel", "cancelled"), ("/server/job_queue/job", "queued"), ("/server/job_queue/pause", "held the queue"),
                              ("/server/files/upload", "uploaded a file")):
                if path.startswith(key):
                    return what, one("filename") or one("filenames")
            if path.startswith("/printer/gcode/script"):
                return "sent", one("script")
        if self.command == "DELETE" and path.startswith("/server/files/gcodes/"):
            return "deleted", urllib.parse.unquote(path.split("/server/files/gcodes/", 1)[1])
        if self.command == "DELETE" and path.startswith("/server/job_queue/job"):
            return "removed from queue", one("job_ids")
        return None

    def guard(self, p):
        """Physical safety checked here, not only in the page: no filament, heater or motion G-code while a job runs
        (the light is allowed), and nothing added to a queue that is running (it would start onto the last print)."""
        if self.command == "POST" and p.startswith("/printer/gcode/script"):
            script = urllib.parse.unquote_plus(p.split("script=", 1)[-1]) if "script=" in p else ""
            if "\n" in script or "\r" in script:
                return "one G-code command at a time"
            if not script.upper().startswith("SET_LED ") and self.printer_busy():
                return "a job is running; that command waits until it ends"
        if self.command == "POST" and p.startswith("/printer/print/start"):
            import printer
            printer.BASE, printer.CAMERA = CFG["moonraker"], CFG["camera"]
            try:
                fname = urllib.parse.unquote(p.split("filename=", 1)[1].split("&")[0]) if "filename=" in p else ""
                missing = printer.heads_not_loaded(fname) if fname else []
            except OSError:
                missing = []
            if missing:
                return "; ".join(missing)
        if self.command == "POST" and p.startswith(("/server/job_queue/job", "/printer/print/start")):
            try:
                with moonraker("GET", "/server/job_queue/status", timeout=5) as r:
                    if json.load(r)["result"]["queue_state"] in ("ready", "loading"):
                        return "the queue is running; hold it first, then add"
            except (OSError, ValueError, KeyError):
                return "could not read the queue"
        return None

    def route(self):
        p = self.path
        if not self.allowed():
            return self.send(403, {"error": "refused: open Workshop by its address, not from another site"})
        if p in ("/", "/index.html"):
            return self.send(200, open(os.path.join(TOOLS, "workshop.html"), "rb").read(), "text/html; charset=utf-8")
        if p.startswith("/moonraker/"):
            path = p[len("/moonraker"):]
            why = self.guard(path)
            if why:
                return self.send(409, {"error": why})
            if self.command == "POST" and path.startswith("/server/job_queue/start"):
                record("started the queue", "", who=actor(self))
                # start one job: hold the queue straight after, so every later job needs its own bed check
                self.proxy(path)
                try:
                    moonraker("POST", "/server/job_queue/pause", timeout=10).read()
                except OSError:
                    pass
                return
            what = self.describe(path)
            if what:
                record(what[0], what[1], who=actor(self))
            return self.proxy(path, timeout=600 if ("upload" in path or "gcode/script" in path) else 30)
        if p.startswith("/cam.jpg"):
            wake_camera()
            return self.proxy(CFG["camera"], timeout=10)
        if p == "/api/designs":
            return self.send(200, designs())
        if p == "/api/info":
            import orca
            try:
                orca.orca_cli()
                slicer = True
            except SystemExit:
                slicer = False
            try:   # Snapmaker U1 Extended firmware (and most Klipper camera stacks) serve a live player here
                with urllib.request.urlopen(f"http://{CFG['printer']}/webcam/", timeout=3) as r:
                    live = f"http://{CFG['printer']}/webcam/" if r.status == 200 else None
            except OSError:
                live = None
            return self.send(200, {"printer": CFG["printer"], "profile": CFG["profile"], "slicer": slicer, "live_camera": live,
                                   "presets": getattr(orca, "PRESET_WORDS", {})})
        if p.startswith("/api/activity"):
            import printer
            try:
                lines = open(printer.ACTIVITY, encoding="utf-8").read().splitlines()[-80:]
            except OSError:
                lines = []
            return self.send(200, [json.loads(x) for x in lines[::-1] if x.strip()])
        if p == "/api/alerts":
            try:
                return self.send(200, alerts())
            except (OSError, ValueError, KeyError) as e:
                return self.send(502, {"error": str(e)})
        if p == "/api/claude" and self.command == "GET":
            return self.send(200, CLAUDE_JOBS[-20:][::-1])
        if p == "/api/claude" and self.command == "POST":
            import threading
            try:
                r = json.loads(self.body() or b"{}")
                text = str(r.get("text", "")).strip()[:1500] if isinstance(r, dict) else ""
            except ValueError:
                text = ""
            if not text:
                return self.send(400, {"error": "say what you want Claude to do"})
            if any(j["state"] == "running" for j in CLAUDE_JOBS):
                return self.send(409, {"error": "Claude is still on the last request; wait for its reply"})
            job = {"id": len(CLAUDE_JOBS) + 1, "who": actor(self), "text": text, "state": "running", "output": "", "t": time.time()}
            CLAUDE_JOBS.append(job)
            record("asked Claude", text[:200], who=job["who"])
            threading.Thread(target=run_claude, args=(job, bool(r.get("bed_clear"))), daemon=True).start()
            return self.send(202, job)
        if p == "/api/filaments":
            import printer
            printer.BASE, printer.CAMERA = CFG["moonraker"], CFG["camera"]
            try:
                return self.send(200, printer.known_filaments())
            except (OSError, ValueError) as e:
                return self.send(502, {"error": f"could not read the printer's filament table: {e}"})
        if p == "/api/filament" and self.command == "POST":
            import printer
            printer.BASE, printer.CAMERA = CFG["moonraker"], CFG["camera"]
            try:
                r = json.loads(self.body() or b"{}")
                if not isinstance(r, dict):
                    raise ValueError
                head, ftype = str(r.get("head", "")), str(r.get("type", ""))
                vendor, sub, colour = str(r.get("vendor") or "generic"), str(r.get("sub") or "generic"), str(r.get("colour", ""))
            except ValueError:
                return self.send(400, {"error": "send head, type, vendor, sub and colour"})
            if not re.fullmatch(r"T?[0-3]", head, re.I):
                return self.send(400, {"error": "head is T0 to T3"})
            try:   # set_filament refuses during a job and checks every name against the printer's own table
                res = printer.set_filament("T" + head.upper().lstrip("T"), ftype, vendor, sub, colour)
                record("set filament", res, who=actor(self))
                return self.send(200, {"result": res})
            except SystemExit as e:
                return self.send(409, {"error": str(e.code)})
        if p == "/api/unload" and self.command == "POST":
            try:
                req = json.loads(self.body() or b"{}")
                head = str(req.get("head", "")) if isinstance(req, dict) else ""
            except ValueError:
                head = ""
            if not re.fullmatch(r"all|T?[0-3]", head, re.I):
                return self.send(400, {"error": "head is T0 to T3, or all"})
            if self.printer_busy():
                return self.send(409, {"error": "a job is running; unload after it ends"})
            start_unload(head)
            record("unload", head, who=actor(self))
            return self.send(202, {"started": head})
        if p == "/api/slice" and self.command == "POST":
            try:
                req = json.loads(self.body() or b"{}")
                code, res = slice_design(req) if isinstance(req, dict) else (400, {"error": "send a JSON object"})
                if code == 200:
                    record("sliced" + (" and queued" if res.get("queued") else ""), f'{res["file"]}: {res["time"]}, {res["total_g"]} g', who=actor(self))
            except (OSError, ValueError, TypeError, AttributeError) as e:
                code, res = 500, {"error": str(e)}
            return self.send(code, res)
        m = re.match(r"/bench/(\d+)/(.+)", p)
        if m and self.command == "GET" and int(m.group(1)) < len(CFG["benches"]):
            root = os.path.realpath(root_of(CFG["benches"][int(m.group(1))]))
            full = os.path.realpath(os.path.join(root, urllib.request.unquote(m.group(2).split("?")[0])))
            rel_parts = os.path.relpath(full, root).split(os.sep)
            if (not full.startswith(root + os.sep) or any(x.startswith(".") for x in rel_parts) or not os.path.isfile(full)
                    or os.path.splitext(full)[1].lower() not in SERVE):
                return self.send(404, {"error": "not found"})
            ctype = {".stl": "model/stl", ".scad": "text/plain; charset=utf-8", ".glb": "model/gltf-binary",
                     ".json": "application/json"}.get(os.path.splitext(full)[1].lower(), "application/octet-stream")
            return self.send(200, open(full, "rb").read(), ctype)
        return self.send(404, {"error": "not found"})

    do_GET = do_POST = do_DELETE = route


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--printer", default=os.environ.get("PRINTER_HOST"), help="printer address (or PRINTER_HOST)")
    ap.add_argument("--moonraker-port", type=int, default=7125)
    ap.add_argument("--camera", default="/server/files/camera/monitor.jpg", help="camera still path on Moonraker")
    ap.add_argument("--bench", action="append", help="a bench.json (repeat for more); default examples/bench.json")
    ap.add_argument("--profile", default="u1", help="tools/orca.py printer setup for slicing")
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--allow-host", action="append", default=[], help="a host name the page may be opened by (IP addresses and localhost always work)")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    if not a.printer:
        sys.exit("give --printer <address> (or set PRINTER_HOST)")
    benches = [os.path.realpath(os.path.expanduser(b)) for b in (a.bench or [os.path.join(REPO, "examples", "bench.json")])]
    CFG.update(printer=a.printer, moonraker=f"http://{a.printer}:{a.moonraker_port}", camera=a.camera, benches=benches,
               profile=a.profile, verbose=a.verbose, allow_hosts=[h.lower() for h in a.allow_host], bed=json.load(open(benches[0], encoding="utf-8")).get("bed", 256))
    import threading
    threading.Thread(target=watch_alerts, daemon=True).start()   # the console scrolls away in minutes; keep warnings
    srv = http.server.ThreadingHTTPServer((a.bind, a.port), Handler)
    print(f"Workshop on http://{a.bind}:{a.port} for printer {a.printer} (Ctrl+C stops)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
