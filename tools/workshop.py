"""Workshop: one page to watch, queue, preview and print on a Klipper/Moonraker printer, with the designs from this repo.

    python tools/workshop.py --printer 192.168.1.50                 serves http://<this machine>:8090
    python tools/workshop.py --printer 192.168.1.50 --bench examples/bench.json --bench ~/my-designs/bench.json --profile u1

What it serves:
    /                     the Workshop page (tools/workshop.html)
    /moonraker/<path>     the printer's Moonraker API, passed through (so the page works from a phone, and later over a VPN)
    /cam.jpg              the printer's camera still (Snapmaker U1 stock firmware: /server/files/camera/monitor.jpg)
    /bench/<n>/<path>     the designs' STLs and sources from bench n, read only
    /api/calibration      GET the recorded calibration results; POST {test, value, filament, note} records one with a photo
    /api/frames[?job=]    the layer photos printer.py watch saved, per job; /frames/<job>/<file>.jpg serves one
    /api/designs          every bench's designs with file links and real slicer numbers for --profile
    /api/slice  (POST)    {"bench": n, "id": design, "preset": "standard", "set": ["infill=30"], "upload": true, "queue": false}
                          packs the design's colour parts into a 3MF, slices it with tools/orca.py and uploads the G-code

Photo watch: with a Claude API key in ANTHROPIC_API_KEY or ~/.config/claude-3d-printing/anthropic-key (mode 600), Claude
looks at the layer photo at layer 2 and every 25 layers. It pauses the print and sends the phone alert only on spaghetti, a
part off the plate or a blob round the nozzle, when it is sure and a second fresh photo agrees; every check is logged in
photo-checks.jsonl beside it. No key, no checks.

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
        every = json.load(open(stats_path, encoding="utf-8")) if os.path.exists(stats_path) else {}
        # keyed by printer name; a --profile given as a folder path falls back to the file's only printer
        stats = every.get(CFG["profile"]) or (next(iter(every.values())) if len(every) == 1 else {})
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


def rgb_gap(a, b):
    """Distance between two colours given as hex (#rrggbb or rrggbb); unknown counts as far."""
    try:
        x, y = (bytes.fromhex(str(v).lstrip("#")[:6]) for v in (a, b))
        return sum((i - j) ** 2 for i, j in zip(x, y))
    except (ValueError, TypeError):
        return 1 << 20


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
    colours = {}
    for n in have:
        try:
            colours[n] = (json.load(open(os.path.join(orca.printer_dir(CFG["profile"]), f"filament-{n}.json"))).get("filament_colour") or [""])[0]
        except (OSError, ValueError, SystemExit):
            pass
    heads = []
    exist = t.get("filament_exist") or [True] * len(t["filament_color_rgba"])
    for ty, sub, c, here in zip(t.get("filament_type", []), t.get("filament_sub_type", []), t["filament_color_rgba"], exist):
        if not here:   # an empty head still reports its last colour: never send a part to it
            heads.append({"colour": None, "material": have[0] if have else "", "label": "empty"})
            continue
        exact = f"{ty}-{sub}".lower().replace(" ", "-") if sub and sub.lower() != "generic" else ty.lower()
        words = [w.lower().replace(" ", "-") for w in (sub, ty) if w and w.lower() != "generic"]
        same = [n for w in words for n in have if w in n.split("-")]
        if len(same) > 1 and colours:   # one profile per colour (petg-black, petg-white...): the nearest colour to the head's
            same.sort(key=lambda n: rgb_gap(colours.get(n), c[:6]))
        pick = (exact if exact in have else None) or (same[0] if same else None) or next(
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
        for x in open(ALERT_LOG, encoding="utf-8").read().splitlines()[-200:]:
            try:
                _alerts.append(json.loads(x))
            except ValueError:
                pass   # a half-written line from a crash
    except OSError:
        pass
    import printer
    printer.BASE, printer.CAMERA = CFG["moonraker"], CFG["camera"]
    import threading
    printer.load_conf()
    last, was, pruned, prev = None, None, 0, None
    checked, watcher = {}, None   # photo watch: last 25-layer block checked per file, the running check
    while True:
        try:
            scan_alerts()
        except Exception as e:   # this thread also takes the photos and timelapses: it must never die
            print("alert scan failed:", e, flush=True)
        try:   # a photo per layer while printing, whoever started the job (the page's Layer photos card)
            s = printer.status()
            if prev and s["state"] != prev:
                alert_change(prev, s)
            prev = s["state"]
            if s["state"] == "printing" and s["layer"] and (s["file"], s["layer"]) != last:
                f = printer.layer_photo(s["file"], s["layer"].split("/")[0])
                last = (s["file"], s["layer"])
                was = os.path.dirname(f)
                n = int(s["layer"].split("/")[0] or 0)
                due = n == 2 or (n and n // PHOTO_EVERY > checked.get(s["file"], 0))
                if due and api_key() and not (watcher and watcher.is_alive()):
                    checked[s["file"]] = n // PHOTO_EVERY
                    watcher = threading.Thread(target=photo_watch, args=(s["file"], n, f), daemon=True)
                    watcher.start()
            if was and s["state"] in ("complete", "cancelled", "error", "standby"):
                # the print ended: a timelapse of its photos, in the background (Khalid, 21 Nov 2026: one per print)
                threading.Thread(target=make_timelapse, args=(was,), daemon=True).start()
                was = None
        except Exception as e:
            print("layer photo failed:", e, flush=True)
        if time.time() - pruned > 3600:
            try:
                for d in printer.prune_photos(30):
                    print("removed photos older than 30 days:", d, flush=True)
            except Exception as e:
                print("photo prune failed:", e, flush=True)
            pruned = time.time()
        time.sleep(20)


CLAUDE_JOBS = []   # [{id, who, text, state, output, t}], newest last; kept in memory while Workshop runs
CLAUDE_TOOLS = [f"Bash(python3 tools/printer.py {c}*)" for c in   # each subcommand named: no raw `gcode`, no notify, no config
                ("status", "snap", "files", "send ", "start ", "queue", "pause", "resume", "cancel", "light ", "unload ", "filament ",
                 "history", "watch", "timelapse")] + [
                "Bash(python3 tools/orca.py *)", "Bash(python3 tools/make_3mf.py *)", "Bash(python3 tools/look.py *)",
                f"Read(/{REPO}/**)"]   # "//" = an absolute path in Claude Code permission rules; benches added per call


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
                            "--max-turns", "30", "--allowedTools", *CLAUDE_TOOLS, *{f"Read(/{os.path.dirname(b)}/**)" for b in CFG.get("benches", [])}],
                           cwd=REPO, env=env, capture_output=True, text=True, timeout=1500)
        d = json.loads(r.stdout or "{}")
        job["output"] = d.get("result") or (r.stderr or "no reply")[-600:]
        job["state"] = "error" if d.get("is_error") or r.returncode else "done"
    except (OSError, ValueError, subprocess.TimeoutExpired) as e:
        job["output"], job["state"] = f"Claude could not run: {e}", "error"
    record("Claude replied", job["output"][:200], who=f"Claude for {job['who']}")


_unloading = []


import threading as _threading
_unload_lock = _threading.Lock()


def start_unload(head):
    """Run printer.unload (the U1 screen's own batch protocol) in a thread; about a minute per head. False if one is running."""
    import printer
    printer.BASE, printer.CAMERA = CFG["moonraker"], CFG["camera"]

    def go():
        try:
            printer.unload(head if head.lower() == "all" else "T" + head.upper().lstrip("T"))
        except SystemExit as e:
            sys.stderr.write(f"unload {head}: {e}\n")

    with _unload_lock:   # two people pressing Unload at once start one unload
        if _unloading and _unloading[0].is_alive():
            return False
        t = _threading.Thread(target=go, daemon=True)
        _unloading[:] = [t]
        t.start()
    return True


_last_wake = [0.0]


def alert_change(prev, s):
    """Phone push (ntfy, if set in printer.json) when a print pauses, finishes or stops, whoever started it."""
    import printer
    f = s.get("file") or "the print"
    took = f'{s["printing_min"] // 60:.0f} h {s["printing_min"] % 60:.0f} min, {s["filament_used_m"]} m of filament' if s.get("printing_min") else ""
    msg = {"paused": (f"Print paused: {f}", (s.get("message") or "Check the printer before resuming.").strip(), "warn"),
           "complete": (f"Print finished: {f}", f"Took {took}. Let the bed cool before taking it off." if took else "Let the bed cool before taking it off.", "good"),
           "cancelled": (f"Print cancelled: {f}", took and f"After {took}.", "bad"),
           "error": (f"Printer error: {f}", (s.get("message") or "Look at the printer.").strip(), "bad")}.get(s["state"])
    if msg and prev in ("printing", "paused"):
        printer.push(*msg, click=CFG.get("url"))
    elif s["state"] == "printing" and prev == "paused":
        printer.push(f"Print resumed: {f}", "", "info", click=CFG.get("url"))


def filament_prices(path):
    """{material: price per kg} from a filament register (a markdown table with a Material column and a price in pounds,
    like the Almehdar repo's registers/filament.md). Rows without a price (to order, from the receipt) are skipped; the
    first priced row per material wins. Empty when no register is set."""
    out = {}
    try:
        rows = [[c.strip() for c in l.strip().strip("|").split("|")] for l in open(path, encoding="utf-8") if l.lstrip().startswith("|")]
    except (OSError, TypeError):
        return out
    if not rows:
        return out
    head = [h.lower() for h in rows[0]]
    if "material" not in head:
        return out
    mi = head.index("material")
    pi = next((i for i, h in enumerate(head) if "price" in h), None)
    for r in rows[2:]:
        if pi is None or len(r) <= max(mi, pi):
            continue
        m = re.search(r"£\s*(\d+(?:\.\d+)?)", r[pi])
        if m and r[mi]:
            out.setdefault(r[mi].upper(), float(m.group(1)))
    return out


CAL_LOG = os.path.join(os.path.expanduser("~"), ".config", "claude-3d-printing", "calibration.jsonl")
CAL_PHOTOS = os.path.join(os.path.dirname(CAL_LOG), "calibration")
CAL_TESTS = {   # test -> the slicer profile key its result belongs in (skills/print-calibration)
    "temperature": "nozzle_temperature", "max-flow": "filament_max_volumetric_speed", "pressure-advance": "pressure_advance",
    "flow-ratio": "filament_flow_ratio", "retraction": "filament_retraction_length", "other": ""}


def calibration(entry=None, who="?"):
    """Calibration results, newest first; with entry, record one (and a camera photo of the test print) first."""
    if entry is not None:
        test, value = str(entry.get("test", "")), str(entry.get("value", "")).strip()[:40]
        if test not in CAL_TESTS:
            raise ValueError("test is one of " + ", ".join(CAL_TESTS))
        if not value:
            raise ValueError("give the value you picked")
        row = {"t": time.time(), "who": who, "test": test, "key": CAL_TESTS[test], "value": value,
               "filament": str(entry.get("filament", "")).strip()[:60], "note": str(entry.get("note", "")).strip()[:300]}
        import printer
        printer.BASE, printer.CAMERA = CFG["moonraker"], CFG["camera"]
        try:
            os.makedirs(CAL_PHOTOS, exist_ok=True)   # kept apart from layer photos: never pruned, never listed as a print
            name = time.strftime("%Y%m%d-%H%M%S") + "-" + test + ".jpg"
            printer.snap(os.path.join(CAL_PHOTOS, name))
            row["photo"] = "/calibration-photo/" + name
        except (OSError, ValueError):
            pass   # a result without a photo is still a result
        os.makedirs(os.path.dirname(CAL_LOG), exist_ok=True)
        with open(CAL_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        record(f"calibration: {test} = {value}", row["filament"], who=who)
    out = []
    try:
        for x in open(CAL_LOG, encoding="utf-8").read().splitlines()[::-1]:
            try:
                out.append(json.loads(x))
            except ValueError:
                pass
    except OSError:
        pass
    return out


# ---------- photo watch: Claude looks at layer photos while nobody is watching (Khalid, 21 Nov 2026) ----------
API_KEY_FILE = os.path.join(os.path.expanduser("~"), ".config", "claude-3d-printing", "anthropic-key")
PHOTO_LOG = os.path.join(os.path.expanduser("~"), ".config", "claude-3d-printing", "photo-checks.jsonl")
PHOTO_MODEL = "claude-opus-5-5"
PHOTO_EVERY = 25          # layers between checks, after the first-layer check at layer 2
PAUSE_ON = {"spaghetti", "detached", "nozzle_blob"}   # clear failures only; cosmetic faults never pause a print
PHOTO_PROMPT = """This is a photo from the camera inside a Snapmaker U1 3D printer, taken during a print at layer {layer}.
The camera sits low at the front, at bed level: it sees the build plate edge-on and the white tool head above it. Glare,
reflections, the dark textured plate, parked tool heads at the back and motion blur are normal.

Decide whether the print has clearly failed in a way that gets worse if it keeps going:
- spaghetti: loose tangles or strands of plastic where the part should be, or extruding into the air
- detached: the part has come off the plate, moved, or been knocked over
- nozzle_blob: a lump of plastic stuck around the nozzle or the tool head
Anything else (stringing, rough surfaces, a small blob on the plate, things you cannot see clearly) is not a failure.

Say pause only when you are sure from what you can see. When unsure or the view is unclear, say ok and lower confidence."""
PHOTO_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["verdict", "problem", "confidence", "reason"],
                "properties": {"verdict": {"type": "string", "enum": ["ok", "pause"]},
                               "problem": {"type": "string", "enum": ["none", "spaghetti", "detached", "nozzle_blob", "other"]},
                               "confidence": {"type": "number"}, "reason": {"type": "string"}}}


def api_keys():
    """Claude API keys for automated work, in order: ANTHROPIC_API_KEY, then anthropic-key, anthropic-key-2 ... beside it,
    each a file only this user can read (one readable by others is skipped, never used). Never logged or shown."""
    keys = [os.environ.get("ANTHROPIC_API_KEY", "").strip()]
    for f in [API_KEY_FILE] + [f"{API_KEY_FILE}-{n}" for n in range(2, 6)]:
        try:
            if not os.stat(f).st_mode & 0o077:
                keys.append(open(f, encoding="utf-8").read().strip())
        except OSError:
            pass
    return [k for k in dict.fromkeys(keys) if k]


def api_key():
    return (api_keys() or [None])[0]


def look_at_photo(jpg, layer):
    """One Claude API call: {verdict, problem, confidence, reason} for a camera frame, or raises OSError/ValueError."""
    import base64
    keys = api_keys()
    if not keys:
        raise ValueError("no API key")
    body = {"model": PHOTO_MODEL, "max_tokens": 2000, "fallbacks": "default",
            "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": PHOTO_SCHEMA}},
            "messages": [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(open(jpg, "rb").read()).decode()}},
                {"type": "text", "text": PHOTO_PROMPT.format(layer=layer)}]}]}
    res, last = None, None
    for n, key in enumerate(keys, 1):   # the next key only when this one is refused, rate-limited or the API is overloaded
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(), method="POST", headers={
            "Content-Type": "application/json", "x-api-key": key, "anthropic-version": "2026-10-21",
            "anthropic-beta": "server-side-fallback-2026-07-01"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                res = json.load(r)
            break
        except urllib.error.HTTPError as e:   # never echo the request: it carries the key
            last = f"Claude API HTTP {e.code} on key {n}"
            if e.code not in (401, 403, 429, 529):
                break
    if res is None:
        raise OSError(last or "Claude API: no answer")
    if res.get("stop_reason") != "end_turn":
        raise ValueError(f"no verdict (stop_reason {res.get('stop_reason')})")
    text = next(b["text"] for b in res["content"] if b.get("type") == "text")
    v = json.loads(text)
    v["usage"] = {k: res.get("usage", {}).get(k) for k in ("input_tokens", "output_tokens")}
    return v


def photo_watch(file, layer, photo):
    """Check a layer photo; pause only on a clear failure that a second, fresh photo confirms. Logs every check."""
    import printer
    def log(row):
        os.makedirs(os.path.dirname(PHOTO_LOG), exist_ok=True)
        with open(PHOTO_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.time(), "file": file, "layer": layer, **row}) + "\n")
    try:
        v = look_at_photo(photo, layer)
        log({"photo": photo, **v})
        if not (v["verdict"] == "pause" and v["problem"] in PAUSE_ON and v["confidence"] >= 0.85):
            return
        time.sleep(8)   # a second, fresh frame must agree before Claude pauses anything on its own
        second = os.path.join(os.path.dirname(photo), time.strftime("%Y%m%d-%H%M%S") + f"-check{layer}.jpg")
        printer.snap(second)
        v2 = look_at_photo(second, layer)
        log({"photo": second, "second_look": True, **v2})
        if not (v2["verdict"] == "pause" and v2["problem"] in PAUSE_ON and v2["confidence"] >= 0.85):
            return
        if printer.status()["state"] != "printing":
            return
        moonraker("POST", "/printer/print/pause", timeout=10).read()
        what = {"spaghetti": "spaghetti", "detached": "the part has come off the plate", "nozzle_blob": "a blob round the nozzle"}[v2["problem"]]
        record(f"paused: Claude saw {what} at layer {layer}", file, who="photo watch")
        printer.push(f"Paused: {what}", f"{os.path.basename(file)} at layer {layer}. {v2['reason']} The print waits for you: "
                     "look at the printer, then resume or cancel.", "bad", click=CFG.get("url"))
    except (OSError, ValueError, KeyError, StopIteration) as e:
        log({"error": str(e)[:200]})


def printer_conf():
    import printer
    return printer.load_conf()


def make_timelapse(folder):
    import printer
    try:
        printer.timelapse(folder)
    except Exception as e:   # never take the watcher down over a video
        print("timelapse failed for", folder, e, flush=True)


def frames(job=""):
    """The layer photos printer.py watch saved: the jobs newest first, or one job's photos in order."""
    import printer
    root = printer.FRAMES
    try:
        jobs = sorted((d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d)) and not d.startswith(".")),
                      key=lambda d: -os.path.getmtime(os.path.join(root, d)))
    except OSError:
        return {"jobs": [], "frames": []}
    def has_pics(d):
        try:
            return any(f.endswith(".jpg") for f in os.listdir(os.path.join(root, d)))
        except OSError:
            return False
    with_pics = [d for d in jobs if has_pics(d)]
    job = job if job in jobs else (with_pics[0] if with_pics else "")   # default: the newest print that has a photo
    try:
        pics = sorted(f for f in os.listdir(os.path.join(root, job)) if f.endswith(".jpg")) if job else []
    except OSError:   # removed between the two listings
        pics = []
    out = []
    for f in pics:
        m = re.match(r"(\d{8})-(\d{2})(\d{2})\d{2}-layer(\d+)\.jpg$", f)
        out.append({"name": f, "url": f"/frames/{urllib.parse.quote(job)}/{f}",
                    "time": f"{m.group(2)}:{m.group(3)}" if m else "", "layer": int(m.group(4)) if m else None})
    video = f"/frames/{urllib.parse.quote(job)}/timelapse.mp4" if job and os.path.isfile(os.path.join(root, job, "timelapse.mp4")) else None
    return {"jobs": jobs, "job": job, "frames": out, "video": video}


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

    def look(self):
        """A picture of a bench design from four sides in its colours, with its size (tools/look.py), as PNG."""
        import subprocess
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        try:
            bench_path = CFG["benches"][int((q.get("bench") or ["0"])[0])]
            d = next(m for m in json.load(open(bench_path, encoding="utf-8"))["models"] if m["id"] == (q.get("id") or [""])[0])
        except (ValueError, IndexError, StopIteration):
            return self.send(404, {"error": "no such design"})
        base = os.path.dirname(bench_path)
        parts = [(os.path.realpath(os.path.join(base, e[1])), e[2]) for e in d["parts"]]
        if not parts or not all(os.path.isfile(f) and f.lower().endswith(".stl") for f, _ in parts):
            return self.send(404, {"error": "this design has no STL parts to draw"})
        with tempfile.TemporaryDirectory() as tmp:
            scad, png = os.path.join(tmp, "d.scad"), os.path.join(tmp, "d.png")
            with open(scad, "w", encoding="utf-8") as f:
                f.write("part = \"all\";\n" + "".join(f'color("{c}") import({json.dumps(stl)}, convexity = 10);\n' for stl, c in parts))
            r = subprocess.run([sys.executable, os.path.join(TOOLS, "look.py"), scad, "--out", png, "--size", "900"],
                               capture_output=True, text=True, timeout=240)
            if r.returncode or not os.path.exists(png):
                return self.send(500, {"error": "could not draw it: " + (r.stderr or r.stdout)[-300:]})
            size = (r.stdout.splitlines() or [""])[0]
            return self.send(200, open(png, "rb").read(), "image/png", {"X-Design-Size": size.replace("size: ", "")})

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
        if self.command == "POST" and p.startswith(("/printer/print/start", "/server/job_queue/start")):
            import printer
            printer.BASE, printer.CAMERA = CFG["moonraker"], CFG["camera"]
            try:
                if p.startswith("/printer/print/start"):
                    fname = urllib.parse.unquote(p.split("filename=", 1)[1].split("&")[0]) if "filename=" in p else ""
                    if not fname:
                        return "say which file to start (filename in the address)"
                else:   # the queue starts its first job: same checks
                    with moonraker("GET", "/server/job_queue/status", timeout=10) as r:
                        jobs = json.load(r)["result"].get("queued_jobs") or []
                    fname = jobs[0]["filename"] if jobs else ""
                missing = printer.heads_not_loaded(fname) if fname else []
            except Exception:   # unknown counts as unsafe, as printer_busy does: refuse rather than guess
                return "could not check the file's heads and bed; try again"
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
            import printer
            printer.BASE, printer.CAMERA = CFG["moonraker"], CFG["camera"]
            wake_camera()
            try:
                with urllib.request.urlopen(printer.camera_url(), timeout=10) as r:
                    return self.send(200, r.read(), r.headers.get("Content-Type", "image/jpeg"))
            except OSError as e:
                return self.send(502, {"error": f"camera: {e}"})
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
            conf = printer_conf()
            push = (f'{conf.get("ntfy_server", "https://ntfy.sh").rstrip("/")}/{conf["ntfy_topic"]}' if conf.get("ntfy_topic") else None)
            return self.send(200, {"printer": CFG["printer"], "profile": CFG["profile"], "slicer": slicer, "live_camera": live, "push": push,
                                   "presets": getattr(orca, "PRESET_WORDS", {}), "prices": filament_prices(CFG.get("register"))})
        m = re.fullmatch(r"/calibration-photo/([\w-]+\.jpg)", p)
        if m:
            f = os.path.join(CAL_PHOTOS, m.group(1))
            return self.send(200, open(f, "rb").read(), "image/jpeg") if os.path.isfile(f) else self.send(404, {"error": "no such photo"})
        if p == "/api/calibration":
            if self.command == "POST":
                try:
                    r = json.loads(self.body() or b"{}")
                    return self.send(201, calibration(r if isinstance(r, dict) else {}, who=actor(self))[0])
                except ValueError as e:
                    return self.send(400, {"error": str(e)})
            return self.send(200, {"tests": CAL_TESTS, "results": calibration()})
        if p.startswith("/api/activity"):
            import printer
            try:
                lines = open(printer.ACTIVITY, encoding="utf-8").read().splitlines()[-80:]
            except OSError:
                lines = []
            out = []
            for x in lines[::-1]:
                try:
                    out.append(json.loads(x))
                except ValueError:
                    pass
            return self.send(200, out)
        if p.startswith("/api/frames"):
            return self.send(200, frames(urllib.parse.parse_qs(urllib.parse.urlsplit(p).query).get("job", [""])[0]))
        m = re.fullmatch(r"/frames/([\w.-]+)/([\w-]+\.(jpg|mp4))", p)
        if m and not m.group(1).startswith("."):
            import printer
            f = os.path.join(printer.FRAMES, m.group(1), m.group(2))
            if not os.path.isfile(f):
                return self.send(404, {"error": "no such file"})
            data = open(f, "rb").read()
            if m.group(3) == "jpg":
                return self.send(200, data, "image/jpeg")
            # video: Safari on iPhone and iPad plays only with byte ranges
            rng = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
            if rng and (rng.group(1) or rng.group(2)):
                a = int(rng.group(1)) if rng.group(1) else max(len(data) - int(rng.group(2)), 0)
                b = min(int(rng.group(2)), len(data) - 1) if rng.group(1) and rng.group(2) else len(data) - 1
                if a > b or a >= len(data):
                    return self.send(416, b"", "video/mp4", {"Content-Range": f"bytes */{len(data)}"})
                return self.send(206, data[a:b + 1], "video/mp4", {"Content-Range": f"bytes {a}-{b}/{len(data)}", "Accept-Ranges": "bytes"})
            return self.send(200, data, "video/mp4", {"Accept-Ranges": "bytes"})
        if p.startswith("/api/look"):
            return self.look()
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
            if not start_unload(head):
                return self.send(409, {"error": "an unload is already running; wait for it to finish"})
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
    ap.add_argument("--camera", help="camera still path or URL (default: the snapshot Moonraker lists)")
    ap.add_argument("--bench", action="append", help="a bench.json (repeat for more); default examples/bench.json")
    ap.add_argument("--profile", default="u1", help="tools/orca.py printer setup for slicing")
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--allow-host", action="append", default=[], help="a host name the page may be opened by (IP addresses and localhost always work)")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--register", help="a filament register (markdown table with Material and a price per kg in pounds) for print costs")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    if not a.printer:
        sys.exit("give --printer <address> (or set PRINTER_HOST)")
    benches = [os.path.realpath(os.path.expanduser(b)) for b in (a.bench or [os.path.join(REPO, "examples", "bench.json")])]
    CFG.update(register=a.register and os.path.expanduser(a.register), url=f"http://{a.bind if a.bind != '0.0.0.0' else 'localhost'}:{a.port}/", printer=a.printer, moonraker=f"http://{a.printer}:{a.moonraker_port}", camera=a.camera, benches=benches,
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
