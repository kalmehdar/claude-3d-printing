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
import datetime
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
BOOT = int(time.time())   # the page reloads itself when this changes (Workshop restarted; an old tab can wedge)
SERVE = {".stl", ".scad", ".3mf", ".glb", ".json", ".png", ".jpg"}   # what /bench may hand out: design files only
SLICE_SETS = {"infill": r"\d{1,3}", "walls": r"\d{1,2}", "supports": r"on|off", "brim": r"auto_brim|outer_only|no_brim",
              "layer": r"0\.(08|12|16|2|20|24|28)", "wall-speed": r"[2-9]\d|1[0-9]\d|200", "wall-accel": r"[5-9]\d\d|[1-9]\d{3}"}
# layer heights are whole Z steps on the U1 (1/75 mm): every one listed above is
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
                    "status": m.get("status", ""), "state": m.get("state", ""), "group": m.get("group", ""),
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
    return slice_parts(d, parts, req)


UPLOADS = os.path.join(os.path.expanduser("~"), ".config", "claude-3d-printing", "uploads")
MODEL_MAX = 100_000_000


def slice_upload(name, data, req):
    """Slice a model sent from the page (STL on one head, picked by colour; 3MF as the file assigns its parts).
    Saved under ~/.config/claude-3d-printing/uploads, then the same path as a bench design: never starts a print."""
    stem, ext = os.path.splitext(os.path.basename(name or ""))
    ext = ext.lower()
    if ext not in (".stl", ".3mf") or not stem:
        return 400, {"error": "send an .stl or .3mf file"}
    if not data or len(data) > MODEL_MAX:
        return 400, {"error": "the file is empty or bigger than 100 MB"}
    os.makedirs(UPLOADS, exist_ok=True)
    path = os.path.join(UPLOADS, safe_name(stem) + ext)
    with open(path, "wb") as f:
        f.write(data)
    colour = req.get("colour") or "#888888"
    if not re.fullmatch(r"#?[0-9A-Fa-f]{6}", str(colour)):
        return 400, {"error": "colour is a hex code like #1c1c1c"}
    d = {"id": safe_name(stem), "name": stem}
    return slice_parts(d, [(stem, path, "#" + str(colour).lstrip("#"))], req)


def pale(colour):
    """White, cream, light grey: the colours a short prime stains."""
    import make_3mf
    r, g, b = make_3mf.rgb(colour)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b > 200


GLUE = ("PETG on the smooth plate: put a thin layer of glue stick (Pritt Stick) where the part sits, then slice again with "
        "glue=yes. Without it PETG can tear the plate's coating.")


def slice_parts(d, parts, req):
    import make_3mf
    import orca
    heads, material = loaded_heads(), ""
    head_note = None
    project = len(parts) == 1 and parts[0][1].lower().endswith(".3mf")   # its own colours: orca.rehome_3mf maps them
    if heads and project:
        material = ",".join(h["material"] for h in heads)
    elif heads:   # a tool changer that reports its filament: put each colour on the head holding it
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
    # prime 20 mm3 a head change by default (Khalid, 22 Nov); 45 when a pale part shares the plate with another colour,
    # where a short prime leaves specks of the last colour (one maker's ~50 tests, not yet proved on the U1)
    if len({c.lower() for _, _, c in parts}) > 1 and any(pale(c) for _, _, c in parts):
        sets.append("prime-volume=45")
    if preset_bad(req.get("preset")):
        return 400, {"error": "unknown preset"}
    on = plate()["plate"]
    sets.append("curr_bed_type=" + PLATES[on])   # slice for the plate that is really on
    used = {h["material"] for h in heads or [] if h.get("material")} if not head_note else \
           {heads[int(n["head"][1:])]["material"] for n in head_note}
    if on == "smooth" and not project and any("petg" in m.lower() for m in used) and not req.get("glue"):
        return 409, {"error": GLUE, "plate": on}   # a 3MF's own colours are known only after slicing: checked in _slice_into
    out = tempfile.mkdtemp(prefix="workshop-")
    try:
        return _slice_into(out, d, parts, heads, material, head_note, preset, sets, req, make_3mf, orca)
    finally:
        shutil.rmtree(out, ignore_errors=True)


def _slice_into(out, d, parts, heads, material, head_note, preset, sets, req, make_3mf, orca):
    try:
        if len(parts) == 1 and parts[0][1].lower().endswith(".3mf"):   # an uploaded 3MF keeps its own parts and heads
            p3 = parts[0][1]
        else:
            data, _ = make_3mf.build(parts, d["name"], CFG["bed"], heads=[h["colour"] for h in heads] if heads else None)
            p3 = os.path.join(out, safe_name(d["id"]) + ".3mf")
            open(p3, "wb").write(data)
        s = orca.slice_([p3], CFG["profile"], preset=preset, material=material, sets=sets, out=out,
                        heads=[h["colour"] or "" for h in heads] if heads else None,
                        substitute=bool(req.get("allow_substitute")))
    except SystemExit as e:   # orca.py and make_3mf stop with a plain message
        return 422, {"error": str(e.code), "hint": "Slicing needs OrcaSlicer (ORCA_CLI) and a printer setup "
                     f"named {CFG['profile']!r} (see tools/orca.py)."}
    if s.get("error") or not s.get("gcode"):
        return 422, {"error": s.get("error", "no G-code")}
    if heads and len(parts) == 1 and parts[0][1].lower().endswith(".3mf") and not req.get("glue") and plate()["plate"] == "smooth":
        # the heads this 3MF really uses (review, 24 Nov: every loaded head used to count); all of them when it kept its own
        used = [heads[int(m["head"][1:])] for m in s.get("model_colours") or []] or heads
        if any("petg" in (h.get("material") or "").lower() for h in used):
            return 409, {"error": GLUE, "plate": "smooth"}
    name = f"{safe_name(d['id'])}-{preset}.gcode"
    result = {"time": s["time"], "grams": s["grams"], "total_g": s["total_g"], "changes": s["changes"],
              "preset": preset, "settings": s["settings"], "file": name,
              "heads": head_note or [{"part": "model colour " + m["colour"], "colour": m["colour"], "head": m["head"]} for m in s.get("model_colours", [])] or None}
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
    checked, watcher = {}, None   # photo watch: last block checked per file, the running check
    head, head_check = None, False   # a check at the first layer after each head change (ooze, missed starts)
    while True:
        try:
            scan_alerts()
        except Exception as e:   # this thread also takes the photos and timelapses: it must never die
            print("alert scan failed:", e, flush=True)
        remember_z()   # the bed's last known Z, so it can come up to the camera later without homing
        try:   # a photo per layer while printing, whoever started the job (the page's Layer photos card)
            s = printer.status()
            if prev and s["state"] != prev:
                alert_change(prev, s, was)
            prev = s["state"]
            if s["state"] == "printing" and s.get("active_head") and head and s["active_head"] != head:
                head_check = True
            head = s.get("active_head") if s["state"] == "printing" else None
            if s["state"] == "printing" and s["layer"] and (s["file"], s["layer"]) != last:
                f = printer.layer_photo(s["file"], s["layer"].split("/")[0])
                last = (s["file"], s["layer"])
                was = os.path.dirname(f)
                n = int(s["layer"].split("/")[0] or 0)
                run = os.path.dirname(f)
                if run not in checked:
                    checked[run] = checked_block(s["file"], run)
                due = (n == 2 and checked[run] < 0) or (n > 2 and n // PHOTO_EVERY > checked[run])
                if (due or head_check) and api_key() and not (watcher and watcher.is_alive()):
                    first = checked[run] < 0   # the first look at this print carries the first-layer plan, whatever layer it lands on
                    checked[run] = max(checked[run], n // PHOTO_EVERY)
                    watcher = threading.Thread(target=photo_watch, args=(s["file"], n, f, first, head_check and not due), daemon=True)
                    watcher.start()
                    head_check = False
            if was and s["state"] in ("complete", "cancelled", "error", "standby"):
                # the print ended: a timelapse of its photos, in the background (Khalid, 21 Nov 2026: one per print)
                threading.Thread(target=make_timelapse, args=(was,), daemon=True).start()
                checked.pop(was, None); _LAST_OK.pop(was, None); head_check = False   # the run is over: nothing carries into the next
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
            try:
                daily_printer_check()
            except Exception as e:   # never take the watcher down over a backup
                print("daily printer check failed:", e, flush=True)
        time.sleep(20)


_unloading = []


import threading as _threading
_unload_lock = _threading.Lock()
_look_slots = _threading.BoundedSemaphore(2)
# a multipart upload field print=true: Moonraker starts the file the moment it lands
UPLOAD_PRINT = re.compile(rb'name="print"[^\r\n]*\r?\n(?:[^\r\n]+\r?\n)*\r?\n\s*true', re.I)


def start_unload(head, action="unload"):
    """Run printer.unload or printer.load (the U1's own feeder batch protocol) in a thread; a few minutes per head.
    False if one is running (load and unload share the lock: one feeder job at a time)."""
    import printer
    printer.BASE, printer.CAMERA = CFG["moonraker"], CFG["camera"]

    def go():
        try:
            getattr(printer, action)(head if head.lower() == "all" else "T" + head.upper().lstrip("T"))
        except SystemExit as e:
            sys.stderr.write(f"{action} {head}: {e}\n")

    with _unload_lock:   # two people pressing Unload at once start one unload
        if _unloading and _unloading[0].is_alive():
            return False
        t = _threading.Thread(target=go, daemon=True)
        _unloading[:] = [t]
        t.start()
    return True


_last_wake = [0.0]


def alert_change(prev, s, folder=None):
    """Phone push (ntfy, if set in printer.json) when a print pauses, finishes or stops, whoever started it."""
    import printer
    f = s.get("file") or "the print"
    took = f'{s["printing_min"] // 60:.0f} h {s["printing_min"] % 60:.0f} min, {s["filament_used_m"]} m of filament' if s.get("printing_min") else ""
    meta = None
    if s["state"] in ("complete", "cancelled", "error") and prev in ("printing", "paused"):
        try:
            with moonraker("GET", "/server/files/metadata?filename=" + urllib.parse.quote(s.get("file") or ""), timeout=10) as r:
                meta = json.load(r)["result"]
        except (OSError, ValueError, KeyError) as e:
            print("print metadata for the alert failed:", e, flush=True)
    c = print_cost(meta, split_use((s.get("filament_used_m") or 0) * 1000, meta)) if meta else 0
    if c:
        took = (took + ", " if took else "") + f"about £{c:.2f}"   # the print's cost on the phone (Khalid, 23 Nov)
    msg = {"paused": (f"Print paused: {f}", (s.get("message") or "Check the printer before resuming.").strip(), "warn"),
           "complete": (f"Print finished: {f}", f"Took {took}. Let the bed cool before taking it off." if took else "Let the bed cool before taking it off.", "good"),
           "cancelled": (f"Print cancelled: {f}", took and f"After {took}.", "bad"),
           "error": (f"Printer error: {f}", (s.get("message") or "Look at the printer.").strip(), "bad")}.get(s["state"])
    if msg and prev in ("printing", "paused"):
        printer.push(*msg, click=CFG.get("url"))
        if s["state"] in ("complete", "cancelled", "error"):   # a part (or what's left of one) now sits on the bed
            bed("part waiting", who="", file=s.get("file") or "")
        if s["state"] in ("error", "cancelled") and s.get("file"):   # what went wrong, while the photos are fresh
            import threading
            threading.Thread(target=print_doctor, args=(s["file"], "the print " + ("stopped with an error: " + (s.get("message") or "") if s["state"] == "error" else "was cancelled")), daemon=True).start()
        if s["state"] == "complete":   # Claude looks at what came out (Khalid, 22 Nov: Claude does the jobs without being asked)
            import threading
            def lift_then_look(f=s.get("file") or ""):
                try:
                    if lift_after_print(f):   # the bed is up at the camera: keep a photo of the part for the gallery
                        gallery_snap(f)
                except (OSError, ValueError, KeyError) as e:
                    print("bed lift or gallery photo after the print failed:", e, flush=True)
                finish_look(f)
            threading.Thread(target=lift_then_look, daemon=True).start()
        if s["state"] in ("complete", "cancelled", "error"):   # what it really used comes off the spools
            try:
                if meta is None:
                    raise OSError("no metadata for " + str(s.get("file")))
                use = take_off_spools(s.get("file"), (s.get("filament_used_m") or 0) * 1000, meta)
                log_print(s, meta, use, folder)
            except (OSError, ValueError, KeyError) as e:
                print("spool update or print log failed:", e, flush=True)
    elif s["state"] == "printing" and prev not in ("printing", "paused"):
        bed("printing", who="", file=f)
    if s["state"] == "printing" and prev == "paused":
        printer.push(f"Print resumed: {f}", "", "info", click=CFG.get("url"))


def print_cost(meta, use, prices=None):
    """£ for a print: each head's measured grams at its material's register price. 0.0 when no price is known."""
    types = str(meta.get("filament_type") or "").split(";")
    prices = filament_prices(CFG.get("register")) if prices is None else prices
    return sum(g * prices.get((types[int(h[1:])] if int(h[1:]) < len(types) else "").upper(), 0) / 1000 for h, g in use.items())


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
PHOTO_EVERY = 10          # layers between checks, after the first-layer check at layer 2 (25 until 22 Nov: Khalid, use the credit)
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
            if os.name == "nt" or not os.stat(f).st_mode & 0o077:   # Windows reports 0o666 whatever the ACL says; the profile folder is per user
                text = open(f, encoding="utf-8").read().strip()   # the bare key, or put-key's ANTHROPIC_API_KEY=... line
                keys.append(text.split("=", 1)[1].strip() if text.startswith("ANTHROPIC_API_KEY=") else text)
        except OSError:
            pass
    return [k for k in dict.fromkeys(keys) if k]


def api_key():
    return (api_keys() or [None])[0]


BEFORE_TEXT = """
The first picture is the same camera at layer {before}, when the watch last looked and found nothing wrong; the second is now.
Compare them part by part. Parts only grow taller between the two. A part that stood in the first picture and is now
missing, shorter, tipped over, moved, or lying elsewhere on the plate has come off: that is detached, even if the rest
looks fine. Loose strands that were not there before, round a part that changed, count as spaghetti."""
PARTS_TEXT = """
The slicer placed {n} separate parts on this plate: {names}. Count what you can see against that."""
HEAD_CHANGE_TEXT = """
A tool head change happened just before this photo. Also look for a blob or string of the previous colour left on the part,
or a line that did not start; only a clear failure counts, as above."""
FIRST_LAYER_TEXT = """
The second picture is a top-down drawing of what the slicer planned for the first layer (blue lines, front edge at the
bottom, 50 mm grid). Compare it with the photo: is there plastic on the plate where the plan has it, and has anything
been dragged, lifted or left out? A first layer is thin and the camera sees it edge-on, so only call detached when a part
or line has clearly lifted, moved or is missing; anything you cannot see clearly is ok with lower confidence."""


def ask_claude(content, schema, effort="low", max_tokens=4000):
    """One Claude API call with structured output; returns the parsed JSON plus usage. Next key on 401/403/429/529.
    Raises OSError (no answer) or ValueError (no verdict)."""
    keys = api_keys()
    if not keys:
        raise ValueError("no API key")
    body = {"model": PHOTO_MODEL, "max_tokens": max_tokens, "fallbacks": "default",
            "output_config": {"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            "messages": [{"role": "user", "content": content}]}
    res, last = None, None
    for n, key in enumerate(keys, 1):
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(), method="POST", headers={
            "Content-Type": "application/json", "x-api-key": key, "anthropic-version": "2026-10-21",
            "anthropic-beta": "server-side-fallback-2026-07-01"})
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                res = json.load(r)
            break
        except urllib.error.HTTPError as e:   # never echo the request: it carries the key
            last = f"Claude API HTTP {e.code} on key {n}"
            if e.code not in (401, 403, 429, 529):
                break
    if res is None:
        raise OSError(last or "Claude API: no answer")
    if res.get("stop_reason") != "end_turn":
        raise ValueError(f"no answer (stop_reason {res.get('stop_reason')})")
    v = json.loads(next(b["text"] for b in res["content"] if b.get("type") == "text"))
    v["usage"] = {k: res.get("usage", {}).get(k) for k in ("input_tokens", "output_tokens")}
    return v


def look_at_photo(jpg, layer, plan=None, prompt=None, schema=None, before=None):
    """One Claude API call: {verdict, problem, confidence, reason} for a camera frame, or raises OSError/ValueError.
    plan: PNG bytes of the first layer's planned paths (the first-layer check), sent as a second picture.
    prompt and schema replace the in-print check (the finished-print look uses its own).
    before: (jpg, layer) of the last frame the watch passed, sent first so a part that snapped or fell off between checks
    shows as a change (22 Nov 2026: a single photo at layer 61 was judged fine with a snapped pillar lying on the plate)."""
    import base64
    keys = api_keys()
    if not keys:
        raise ValueError("no API key")
    body = {"model": PHOTO_MODEL, "max_tokens": 2000, "fallbacks": "default",
            "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": schema or PHOTO_SCHEMA}},
            "messages": [{"role": "user", "content": [
                *([{"type": "text", "text": f"Earlier, layer {before[1]}:"},
                   {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(open(before[0], "rb").read()).decode()}},
                   {"type": "text", "text": f"Now, layer {layer}:"}] if before else []),
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(open(jpg, "rb").read()).decode()}},
                *([{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(plan).decode()}}] if plan else []),
                {"type": "text", "text": prompt or PHOTO_PROMPT.format(layer=layer) + (FIRST_LAYER_TEXT if plan else "")}]}]}
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


FINISH_PROMPT = """This photo is from the camera inside a Snapmaker U1 3D printer, taken at the last layer of the print {file}, as it finished.
The camera sits low at the front, at bed level, so it sees the part from the side; glare, the dark textured plate, parked
tool heads and the white tool head are normal. Judge only what you can see of the finished part:
- quality: good (nothing wrong you can see), flaws (it finished but shows faults) or failed (spaghetti, a fallen or
  half-printed part, a large mess)
- flaws: any of stringing, warping (corners lifted), layer_shift, blobs, poor_bridging (sagging overhangs or bridges),
  incomplete, other; empty when good
- reason: one or two plain sentences on what you see
- advice: one sentence on what to change next time (a slicer setting, a check, or nothing)
When the view is unclear, say good and say so in the reason."""
FINISH_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["quality", "flaws", "reason", "advice"],
                 "properties": {"quality": {"type": "string", "enum": ["good", "flaws", "failed"]},
                                "flaws": {"type": "array", "items": {"type": "string", "enum": ["stringing", "warping", "layer_shift", "blobs",
                                                                                                "poor_bridging", "incomplete", "other"]}},
                                "reason": {"type": "string"}, "advice": {"type": "string"}}}


def last_layer_photo(file, frames=None):
    """The highest-layer photo of this file's newest print run (<stem>-<job id>), taken while the part was still in view:
    after a print the U1 drops the bed, so a fresh photo misses the part. None when there is none."""
    import printer
    root = frames or printer.FRAMES
    stem = re.sub(r"[^A-Za-z0-9_.-]", "_", os.path.splitext(os.path.basename(file or "job"))[0]).lstrip(".") or "job"
    try:
        runs = [d for d in os.listdir(root) if re.fullmatch(re.escape(stem) + r"-[0-9A-Fa-f]+", d)]   # Moonraker job ids are hex (00000A)
    except OSError:
        return None
    for d in sorted(runs, key=lambda d: int(d.rsplit("-", 1)[1], 16), reverse=True):
        shots = [(int(m.group(1)), f) for f in os.listdir(os.path.join(root, d)) if (m := re.search(r"-layer(\d+)\.jpg$", f))]
        if shots:
            return os.path.join(root, d, max(shots)[1])
    return None


def finish_look(file, wait=20):
    """After a print finishes: one fresh photo, Claude's verdict on the part, logged beside the photo checks, in the activity
    feed and on the phone. Read-only: it never moves, heats or starts anything."""
    import printer
    if not api_key():
        return None
    time.sleep(wait)   # the last layer's photo lands first
    photo = last_layer_photo(file)
    row = {"t": time.time(), "file": file, "final": True, "photo": photo}
    try:
        if not photo:   # no layer photos for this run: a fresh one, which may miss the part once the bed has dropped
            folder = os.path.join(os.path.dirname(PHOTO_LOG), "finished")
            os.makedirs(folder, exist_ok=True)
            photo = row["photo"] = os.path.join(folder, time.strftime("%Y%m%d-%H%M%S-") + re.sub(r"[^\w.-]", "_", os.path.basename(file or "print")) + ".jpg")
            printer.snap(photo)
        v = look_at_photo(photo, None, prompt=FINISH_PROMPT.format(file=os.path.basename(file or "the print")), schema=FINISH_SCHEMA)
        row.update(v)
        flaws = ", ".join(f.replace("_", " ") for f in v["flaws"])
        record(f"looked at the finished print: {v['quality']}" + (f" ({flaws})" if flaws else ""), file, who="photo watch")
        if v["quality"] != "good":
            print_doctor(file, f"the finished part looked {v['quality']}" + (f" ({flaws})" if flaws else "") + f": {v['reason']}")
        printer.push(f"Claude's look: {v['quality']}" + (f", {flaws}" if flaws else ""), f"{os.path.basename(file or '')}. {v['reason']} {v['advice']}".strip(),
                     {"good": "good", "flaws": "warn", "failed": "bad"}[v["quality"]], click=CFG.get("url"))
    except (OSError, ValueError, KeyError, StopIteration) as e:
        row["error"] = "finished-print look: " + str(e)[:180]
    with open(PHOTO_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return row


DOCTOR = os.path.join(os.path.dirname(PHOTO_LOG), "doctor")
DOCTOR_KEYS = ("layer_height", "nozzle_temperature", "bed_temperature", "outer_wall_speed", "inner_wall_speed", "travel_speed",
               "retraction_length", "retraction_speed", "z_hop", "z_hop_types", "wipe", "reduce_crossing_wall", "wall_loops",
               "sparse_infill_density", "sparse_infill_pattern", "prime_volume", "fan_max_speed", "fan_min_speed",
               "slow_down_layer_time", "enable_support", "brim_type", "filament_type", "filament_max_volumetric_speed",
               "pressure_advance", "seam_position", "first_layer_height", "first_layer_speed", "wall_sequence",
               "seam_slope_type", "overhang_fan_speed", "outer_wall_acceleration", "default_acceleration", "ironing_type",
               "resolution", "slice_closing_radius", "top_bottom_infill_wall_overlap", "z_offset")
DOCTOR_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["diagnosis", "causes", "changes", "next_step"],
                 "properties": {"diagnosis": {"type": "string"},
                                "causes": {"type": "array", "items": {"type": "string"}},
                                "changes": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                            "required": ["setting", "now", "suggested", "why"],
                                            "properties": {"setting": {"type": "string"}, "now": {"type": "string"},
                                                           "suggested": {"type": "string"}, "why": {"type": "string"}}}},
                                "next_step": {"type": "string"}}}
DOCTOR_PROMPT = """You are diagnosing a 3D print on a Snapmaker U1 (four tool heads, PETG and PLA, OrcaSlicer). What happened: {reason}.
The pictures are the print's layer photos in order, oldest first, from a low camera at bed level (glare, the dark textured
plate and parked heads are normal). Below are the slicer settings it used and the photo watch's notes.

Say plainly what went wrong and why, list the likely causes most likely first, and suggest at most four slicer setting
changes using the exact setting names given (now and suggested values). Only suggest a change the evidence supports; if the
photos do not show the fault clearly, say so and suggest a check instead. next_step is one sentence: what to print or check
next.

Known causes to weigh against the evidence (from makers' tested videos and this printer's own failures, 22 Nov 2026):
- Stringing on PETG: wet filament first, then too hot, then retraction. A popping sound a few seconds after retraction
  means too much retraction (air drawn in), which gives blobs and pinholes. Direct drive rarely needs over 1.5 mm.
- Gashes or dragged lines where a feature joins the main part: PETG sticking to the nozzle; lower the temperature
  before lowering speed. Pressure advance does not cure it.
- PETG fused hard to the plate or tearing the plate: die swell; a small positive Z offset (0.05 to 0.1 mm) cures it.
- A line round the outer wall exactly where a big flat layer (lid, floor, ironing) starts: shrink of the big layer
  pulling the walls; chamfer the edge, print walls inner-outer-inner, keep speeds even. Longer layer times make it worse.
- A thin part snapping at one height: a cold, weak layer where the printer spent minutes on something else at that
  height (ironing, a big top layer, a long tool change), then the nozzle knocked it. Seen on this U1 at 3 mm on 22 Nov.
- Fine vertical lines evenly spaced about 2 mm apart: belt pitch (belt tension); irregular ones that change with
  speed: stepper resonance. Slower can be worse. Input shaper does not fix either.
- Dark bands on translucent PETG walls at regular spacing: infill showing through and cooling unevenly (gyroid turning).
- Seam blobs or a zip on round parts: scarf seam; on corners an aligned or back seam hides better.
- One head misaligned after the printer was moved, or after a nozzle swap: rerun nozzle offset calibration and the mesh.
- Dirty-contact warnings or failed pickups: pogo pins and steel connectors need cleaning; dust is the usual cause.
- Small parts knocked off mid-print, then others in a domino: add a brim to small parts.

Slicer settings:
{settings}

Photo watch notes (newest last):
{notes}"""


def print_doctor(file, reason, photos=8):
    """After a pause, failure or flawed finish: Claude reads the print's layer photos and its slicer settings and suggests
    what to change. Saved to ~/.config/claude-3d-printing/doctor/, in the activity feed and on the phone. Read-only: it
    changes no setting and touches nothing on the printer. Returns the report, or None."""
    import base64
    import printer
    if not api_key() or not file:
        return None
    shots = []
    last = last_layer_photo(file)
    if last:
        folder = os.path.dirname(last)
        layered = sorted(((int(m.group(1)), os.path.join(folder, f)) for f in os.listdir(folder)
                          if (m := re.search(r"-layer(\d+)\.jpg$", f))))
        step = max(1, len(layered) // photos)
        shots = [p for _, p in layered[::step]][-photos:]
    try:
        tail = printer.fetch("/server/files/gcodes/" + urllib.parse.quote(file), tail=120_000).decode("utf-8", "replace")
        settings = "\n".join(l[2:] for l in tail.splitlines() if l.startswith("; ") and l[2:].split(" = ")[0] in DOCTOR_KEYS)
    except OSError as e:
        settings = f"(could not read the G-code: {e})"
    notes = []
    try:
        for x in open(PHOTO_LOG, encoding="utf-8").read().splitlines()[-60:]:
            d = json.loads(x)
            if d.get("file") == file and (d.get("reason") or d.get("error")):
                notes.append(f'layer {d.get("layer")}: {d.get("verdict") or d.get("quality") or "error"} {d.get("problem") or ""} {d.get("reason") or d.get("error")}'.strip())
    except (OSError, ValueError):
        pass
    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(open(p, "rb").read()).decode()}} for p in shots]
    content.append({"type": "text", "text": DOCTOR_PROMPT.format(reason=reason, settings=settings or "(none found)", notes="\n".join(notes[-12:]) or "(none)")})
    row = {"t": time.time(), "file": file, "reason": reason, "photos": [os.path.basename(p) for p in shots]}
    try:
        row.update(ask_claude(content, DOCTOR_SCHEMA, effort="medium"))
        record("print doctor: " + row["diagnosis"][:120], file, who="Claude")
        printer.push("Print doctor: " + os.path.basename(file), row["diagnosis"][:300] + (" Try: " + "; ".join(f'{c["setting"]} {c["now"]} to {c["suggested"]}' for c in row["changes"][:3]) if row["changes"] else ""),
                     "warn", click=CFG.get("url"))
    except (OSError, ValueError, KeyError, StopIteration) as e:
        row["error"] = str(e)[:200]
    os.makedirs(DOCTOR, exist_ok=True)
    with open(os.path.join(DOCTOR, time.strftime("%Y%m%d-%H%M%S-") + safe_name(os.path.basename(file)) + ".json"), "w", encoding="utf-8") as f:
        json.dump(row, f, indent=1)
    return row


def checked_block(file, run=None):
    """The highest block already checked for this print run, from the log, so a Workshop restart does not check again.
    run: the run's photo folder; a reprint of the same file is a new run and starts fresh (22 Nov 2026 review)."""
    best = -1
    try:
        for x in open(PHOTO_LOG, encoding="utf-8").read().splitlines()[-200:]:
            try:
                d = json.loads(x)
                if d.get("file") == file and d.get("layer") is not None and (run is None or os.path.dirname(d.get("photo") or "") == run):
                    best = max(best, int(d["layer"]) // PHOTO_EVERY)
            except (ValueError, TypeError):
                pass
    except OSError:
        pass
    return best


_LAST_OK = {}   # run folder -> (photo, layer) the watch last passed: the "before" picture for its next look


def photo_watch(file, layer, photo, first=None, head_change=False):
    """Check a layer photo; pause only on a clear failure that a second, fresh photo confirms. Logs every check."""
    import printer
    def log(row):
        os.makedirs(os.path.dirname(PHOTO_LOG), exist_ok=True)
        with open(PHOTO_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.time(), "file": file, "layer": layer, **row}) + "\n")
    plan = None
    if first if first is not None else layer <= 2:   # the first-layer check: what the slicer planned for layer 1 goes beside the photo
        try:
            plan = layer1_png(file)
        except Exception as e:
            log({"error": "no first-layer plan: " + str(e)[:150]})
    before = _LAST_OK.get(os.path.dirname(photo))   # this run only: a reprint never compares with the last run
    extra = BEFORE_TEXT.format(before=before[1]) if before else ""
    try:
        names = [o["name"] for o in parts()["objects"]]
        if len(names) > 1:
            extra += PARTS_TEXT.format(n=len(names), names=", ".join(names)[:600])
    except Exception:   # the check still runs without the parts list
        pass
    try:
        prompt = PHOTO_PROMPT.format(layer=layer) + extra + (FIRST_LAYER_TEXT if plan else "") + (HEAD_CHANGE_TEXT if head_change else "")
        v = look_at_photo(photo, layer, plan, prompt=prompt, before=before)
        log({"photo": photo, "first_layer": bool(plan), "head_change": head_change, **v})
        sure = 0.8 if before else 0.85   # a change against a frame that passed is firmer evidence than one photo alone
        if not (v["verdict"] == "pause" and v["problem"] in PAUSE_ON and v["confidence"] >= sure):
            if v["verdict"] == "ok" and v["problem"] == "none":
                _LAST_OK[os.path.dirname(photo)] = (photo, layer)
            return
        time.sleep(8)   # a second, fresh frame must agree before Claude pauses anything on its own
        second = os.path.join(os.path.dirname(photo), time.strftime("%Y%m%d-%H%M%S") + f"-check{layer}.jpg")
        printer.snap(second)
        v2 = look_at_photo(second, layer, plan, prompt=prompt, before=before)
        log({"photo": second, "second_look": True, **v2})
        if not (v2["verdict"] == "pause" and v2["problem"] in PAUSE_ON and v2["confidence"] >= sure):
            return
        if printer.status()["state"] != "printing":
            return
        moonraker("POST", "/printer/print/pause", timeout=10).read()
        what = {"spaghetti": "spaghetti", "detached": "the part has come off the plate", "nozzle_blob": "a blob round the nozzle"}[v2["problem"]]
        record(f"paused: Claude saw {what} at layer {layer}", file, who="photo watch")
        printer.push(f"Paused: {what}", f"{os.path.basename(file)} at layer {layer}. {v2['reason']} The print waits for you: "
                     "look at the printer, then resume or cancel.", "bad", click=CFG.get("url"))
        print_doctor(file, f"the photo watch paused it at layer {layer}: {what}")
    except (OSError, ValueError, KeyError, StopIteration) as e:
        log({"error": str(e)[:200]})


_PLANS = {}   # file -> first-layer paths, read once per file from the printer


def layer1_paths(file):
    """The first layer's extrusion paths, read from the start of the G-code on the printer (it stops reading at layer 2)."""
    if file not in _PLANS:
        import bedmap
        with moonraker("GET", "/server/files/gcodes/" + "/".join(urllib.parse.quote(x) for x in file.split("/")), timeout=60) as r:
            _PLANS[file] = bedmap.first_layer(line.decode("utf-8", "replace") for line in r)
    return _PLANS[file]


def layer1_png(file):
    import bedmap
    return bedmap.render(CFG.get("bed", 270), layer1_paths(file))


# ---------- cancel one part: ask first, then only that part (Khalid, 21 Nov 2026) ----------
PART_NAME = re.compile(r"[A-Za-z0-9_.\-]{1,120}")


def parts():
    """The parts the running print defines (EXCLUDE_OBJECT_DEFINE), which are cancelled, which prints now, which are left."""
    with moonraker("GET", "/printer/objects/query?exclude_object&print_stats=filename,state", timeout=10) as r:
        st = json.load(r)["result"]["status"]
    e, ps = st.get("exclude_object") or {}, st.get("print_stats") or {}
    objs, done = e.get("objects") or [], e.get("excluded_objects") or []
    return {"file": ps.get("filename") or "", "state": ps.get("state") or "", "objects": objs, "excluded": done,
            "current": e.get("current_object"), "left": [o["name"] for o in objs if o["name"] not in done]}


def parts_png(mark=None):
    import bedmap
    p = parts()
    try:
        paths = layer1_paths(p["file"]) if p["file"] else []
    except Exception:   # the outline picture still works without the first-layer drawing
        paths = []
    return bedmap.render(CFG.get("bed", 270), paths, p["objects"], mark=mark, done=p["excluded"])


def cancel_part(name, who):
    """Stop printing one part (EXCLUDE_OBJECT NAME=...) and keep the rest going. Never the last part left: that would be
    the whole print, which stays a person's own cancel. Returns (status, body)."""
    if not PART_NAME.fullmatch(name or ""):
        return 400, {"error": "not a part name"}
    p = parts()
    if p["state"] not in ("printing", "paused"):
        return 409, {"error": "nothing is printing"}
    if name not in p["left"]:
        return 400, {"error": f"{name} is not a part still printing"}
    if len(p["left"]) < 2:
        return 409, {"error": "it is the only part left, so cancelling it would stop the whole print; cancel the print itself instead"}
    moonraker("POST", "/printer/gcode/script?script=" + urllib.parse.quote(f"EXCLUDE_OBJECT NAME={name}"), timeout=20).read()
    record(f"cancelled one part: {name}", p["file"], who=who)
    return 200, {"ok": True, "cancelled": name, "left": [n for n in p["left"] if n != name]}


# ---------- print log: one row in the print register when a print ends (Khalid, 21 Nov 2026) ----------
OUTCOME = {"complete": "Completed", "cancelled": "Cancelled", "error": "Stopped by an error"}
PRESET_IN_NAME = re.compile(r"-(draft|standard|strong|solid|fine|quality|polished|low-waste)\.gcode$")


def log_print(s, meta, use, folder=None):
    """Append a row to the print register (--print-log, a markdown table like the Almehdar repo's registers/prints.md).
    Columns: Date | Model (file) | For | Material and heads | Settings | Grams | Time | Cost | Outcome | KeyProof?
    '?' where only a person knows. Returns the row, or None when no register is set or it has no such table."""
    path = CFG.get("print_log")
    if not path or not os.path.exists(path):
        return None
    lines = open(path, encoding="utf-8").read().split("\n")
    head = next((i for i, l in enumerate(lines) if l.startswith("| Date |")), None)
    if head is None:
        return None
    end = head
    while end + 1 < len(lines) and lines[end + 1].startswith("|"):
        end += 1
    types = [t for t in str(meta.get("filament_type") or "").split(";")]
    sp = spools()
    heads, cost = [], print_cost(meta, use)
    for h, g in use.items():
        i = int(h[1:])
        mat = types[i] if i < len(types) else ""
        heads.append(f'{(sp.get(h) or {}).get("spool") or mat or "?"} on {h}')
    file = s.get("file") or "?"
    m = PRESET_IN_NAME.search(file)
    outcome = OUTCOME.get(s.get("state"), s.get("state") or "?")
    if folder:
        outcome += f"; layer photos {os.path.basename(folder)} (timelapse.mp4 beside them once made)"
    cell = lambda v: str(v).replace("|", "/").replace("\n", " ")
    row = "| " + " | ".join(cell(v) for v in [time.strftime("%Y-%m-%d"), file, "?", ", ".join(heads) or "?",
                                               (m.group(1) if m else "?"), f"{sum(use.values()):.1f} (measured)" if use else "?",
                                               f'{s.get("printing_min") or 0:.0f} min', f"£{cost:.2f}" if cost else "?", outcome, "?"]) + " |"
    lines.insert(end + 1, row)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    record("logged in the print register", file, who="Workshop")
    return row


REQUESTS = os.path.join(os.path.expanduser("~"), ".config", "claude-3d-printing", "requests.json")


_requests_lock = _threading.Lock()


def requests_(change=None, who="?"):
    """Print requests between two people: [{id, who, what, design, qty, colour, note, state, by, t}], newest first.
    change: {"what"...} adds one; {"id", "state": "taken" | "done" | "dropped"} moves one along. One at a time (a lock),
    so two people adding at once neither lose one nor get the same id."""
    with _requests_lock:
        return _requests(change, who)


def _requests(change, who):
    try:
        rows = json.load(open(REQUESTS, encoding="utf-8"))
    except (OSError, ValueError):
        rows = []
    if change:
        if change.get("id"):
            r = next((r for r in rows if r["id"] == change["id"]), None)
            if not r:
                raise ValueError("no such request")
            if change.get("state") not in ("open", "taken", "done", "dropped"):
                raise ValueError("state is open, taken, done or dropped")
            r.update(state=change["state"], by=who, t_state=time.time())
            record(f"request {change['state']}: {r['what']}", "", who=who)
        else:
            what = str(change.get("what", "")).strip()[:120]
            if not what:
                raise ValueError("say what to print")
            qty = int(change.get("qty") or 1)
            if not 1 <= qty <= 50:
                raise ValueError("quantity is 1 to 50")
            r = {"id": (max([x["id"] for x in rows], default=0) + 1), "who": who, "what": what,
                 "design": str(change.get("design", ""))[:80], "qty": qty, "colour": str(change.get("colour", ""))[:30],
                 "note": str(change.get("note", "")).strip()[:300], "state": "open", "by": "", "t": time.time()}
            rows.insert(0, r)
            record(f"asked for a print: {qty} x {what}" + (f" in {r['colour']}" if r["colour"] else ""), "", who=who)
        os.makedirs(os.path.dirname(REQUESTS), exist_ok=True)
        json.dump(rows[:200], open(REQUESTS + ".part", "w"), indent=1)
        os.replace(REQUESTS + ".part", REQUESTS)
    return rows


BED = os.path.join(os.path.expanduser("~"), ".config", "claude-3d-printing", "bed.json")


def bed(state=None, who="", file=""):
    """The bed between two people: {"state": "clear" | "part waiting" | "printing", "file", "who", "t"}; state saves."""
    if state:
        os.makedirs(os.path.dirname(BED), exist_ok=True)
        json.dump({"state": state, "file": file, "who": who, "t": time.time()}, open(BED + ".part", "w"))
        os.replace(BED + ".part", BED)
    try:
        return json.load(open(BED, encoding="utf-8"))
    except (OSError, ValueError):
        return {"state": "unknown"}


PLATE = os.path.join(os.path.expanduser("~"), ".config", "claude-3d-printing", "plate.json")
PLATES = {"textured": "Textured PEI Plate", "smooth": "High Temp Plate"}   # OrcaSlicer's names; smooth PEI is "High Temp"


def plate(name=None, who=""):
    """Which build plate is on the U1: {"plate": "textured" | "smooth", "who", "t"}; textured until someone says otherwise.
    Slicing uses it, and PETG on the smooth plate needs a glue layer first (Khalid, 22 Nov 2026: PETG can tear smooth PEI)."""
    if name:
        os.makedirs(os.path.dirname(PLATE), exist_ok=True)
        json.dump({"plate": name, "who": who, "t": time.time()}, open(PLATE + ".part", "w"))
        os.replace(PLATE + ".part", PLATE)
    try:
        p = json.load(open(PLATE, encoding="utf-8"))
        return p if p.get("plate") in PLATES else {"plate": "textured"}
    except (OSError, ValueError):
        return {"plate": "textured"}


MAINT = os.path.join(os.path.expanduser("~"), ".config", "claude-3d-printing", "maintenance.json")
# Snapmaker's own U1 schedule (wiki.snapmaker.com/en/snapmaker_u1/troubleshooting/Core_Components_Maintenance_Guide and
# /PEI_steel_sheet_cleaning, read 23 Nov 2026): (what, unit, every); unit is "prints" or "days"
MAINT_TASKS = {
    "plate": ("Wash the PEI plate: dish soap, sponge, water, once below 40 C (never acetone)", "prints", 10),   # "every 5-10 prints"
    "shafts": ("Clean the steel linear shafts and add a little white lithium grease", "days", 30),           # "Monthly"
    "xrod": ("Wipe the X carbon rod with isopropanol: never grease it", "days", 30),                         # "Monthly"
    "belts": ("Check the XY belt tension and wipe the teeth dry", "days", 30),                                # "Monthly"
    "zscrew": ("Clean and grease the Z lead screw (the grease that came with the U1)", "days", 91),            # "Quarterly"
    "pins": ("Clean and grease the steel pins and balls at the arrow marks", "days", 91),                    # "Quarterly"
    "pogo": ("Wipe the tool-head pogo pins with a barely damp alcohol cloth, one direction", "days", 150),    # "Every 4-6 months"
}


def print_count():
    """Prints the U1 has run, from Moonraker's history totals."""
    return _get("/server/history/totals", timeout=10)["job_totals"]["total_jobs"]


def maintenance(done=None, who="", jobs=None, now=None):
    """Each task with how far it is into its interval (prints or days) and whether it is due; done=task marks it done now.
    A task never marked done counts from when Workshop first saw it, not from the printer's first day."""
    jobs = print_count() if jobs is None else jobs
    now = time.time() if now is None else now
    try:
        data = json.load(open(MAINT, encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    changed = False
    for k in MAINT_TASKS:
        if "at_jobs" not in data.get(k, {}):   # new, or from the old print-hours version: start counting now
            data[k], changed = {"at_jobs": jobs, "t": now, "who": ""}, True
    if done:
        if done not in MAINT_TASKS:
            raise ValueError("task is one of " + ", ".join(MAINT_TASKS))
        data[done], changed = {"at_jobs": jobs, "t": now, "who": who}, True
    if changed:
        os.makedirs(os.path.dirname(MAINT), exist_ok=True)
        json.dump(data, open(MAINT + ".part", "w"), indent=1)
        os.replace(MAINT + ".part", MAINT)
    rows = []
    for k, (what, unit, every) in MAINT_TASKS.items():
        since = int(jobs - data[k]["at_jobs"]) if unit == "prints" else int((now - data[k]["t"]) // 86400)   # Moonraker counts jobs as a float
        rows.append({"task": k, "what": what, "unit": unit, "every": every, "since": since, "due": since >= every,
                     "who": data[k]["who"], "t": data[k]["t"]})
    return rows


SPOOLS = os.path.join(os.path.expanduser("~"), ".config", "claude-3d-printing", "spools.json")
LOW_G = 200   # warn when a spool drops under this (Almehdar register: flag a reorder under 200 g)


def spools(data=None):
    """{"T0": {"spool", "start_g", "used_g", "since"}, ...}: the spool on each head and what it has used; data saves."""
    if data is not None:
        os.makedirs(os.path.dirname(SPOOLS), exist_ok=True)
        json.dump(data, open(SPOOLS + ".part", "w"), indent=1)
        os.replace(SPOOLS + ".part", SPOOLS)
        return data
    try:
        return json.load(open(SPOOLS, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def filament_short(meta, data=None, spare=10):
    """Heads whose spool, by Workshop's count, has less left than the file needs (plus a spare few grams):
    ["T1 SUNLU PETG black needs 120 g, about 80 g left", ...]. Heads with no spool recorded are not judged."""
    data = spools() if data is None else data
    out = []
    for i, need in enumerate(meta.get("filament_weight") or []):
        sp = data.get(f"T{i}")
        if not need or not sp:
            continue
        left = sp["start_g"] - sp.get("used_g", 0)
        if need + spare > left:
            out.append(f'T{i} {sp["spool"]} needs {need:.0f} g, about {max(left, 0):.0f} g left')
    return out


def split_use(actual_mm, meta):
    """Grams per head for a finished or stopped print: the printer's measured total, shared out in the slicer's per-head
    proportions (stock firmware reports one total), each head's mm turned to grams with the slicer's own density."""
    mm, g = meta.get("filament_used_mm") or [], meta.get("filament_weight") or []
    total = sum(mm)
    if not total or not actual_mm:
        return {}
    return {f"T{i}": round(actual_mm * (m / total) * (g[i] / m), 1) for i, m in enumerate(mm) if m and i < len(g)}


def take_off_spools(file, actual_mm, meta):
    """After a print: grams off each head's spool, a log line, and a phone alert for any spool now under LOW_G."""
    import printer
    use, data = split_use(actual_mm, meta), spools()
    low = []
    for head, grams in use.items():
        sp = data.get(head)
        if not sp:
            continue
        sp["used_g"] = round(sp.get("used_g", 0) + grams, 1)
        left = sp["start_g"] - sp["used_g"]
        if left < LOW_G <= left + grams:
            low.append(f'{head} {sp["spool"]}: about {max(left, 0):.0f} g left')
    if use:
        spools(data)
        record("filament used: " + ", ".join(f"{h} {g} g" for h, g in use.items()), file, who="Workshop")
    if low:
        printer.push("Spool running low", "; ".join(low) + ". Time to reorder (the Almehdar register flags under 200 g).", "warn",
                     click=CFG.get("url"))
    return use


def _get(path, timeout=15):
    with moonraker("GET", path, timeout=timeout) as r:
        return json.load(r)["result"]


def insight(kind):
    """The data behind the Printer tab, the cost card and the planner. Reads only."""
    import insights, printer
    if kind == "health":
        st = _get("/printer/objects/query?bed_mesh=probed_matrix,mesh_min,mesh_max,profile_name")["status"]
        temps = _get("/server/temperature_store?include_monitors=false")
        keep = {k: {f: v[f][::20] for f in ("temperatures", "targets") if f in v} for k, v in temps.items()}   # the last 20 minutes, every 20 s
        return {"mesh": insights.mesh(st.get("bed_mesh")), "temps": keep}
    if kind == "costs":
        jobs = _get("/server/history/list?limit=300&order=desc")["jobs"]
        starters = {}
        try:   # who pressed start, from the activity log (Workshop and printer.py both write it)
            for line in open(printer.ACTIVITY, encoding="utf-8"):
                try:
                    a = json.loads(line)
                except ValueError:
                    continue
                if a.get("what") in ("started", "started the queue") and a.get("detail"):
                    starters[(a["detail"], int(a["t"] // 60))] = a.get("who", "someone")
        except OSError:
            pass
        def who(j):   # a start logged within two minutes either side of the job's start time
            t = int((j.get("start_time") or 0) // 60)
            return next((starters[(j.get("filename"), t + d)] for d in (0, -1, 1, -2, 2) if (j.get("filename"), t + d) in starters), None)
        found = {(j.get("filename"), int((j.get("start_time") or 0) // 60)): w for j in jobs if (w := who(j))}
        out = insights.costs(jobs, filament_prices(CFG.get("register")), found)
        out["prints"] = [{**r, "when": r["when"].isoformat(timespec="minutes")} for r in out["prints"][:60]]
        return out
    if kind == "plan":
        q = _get("/server/job_queue/status").get("queued_jobs") or []
        ps = _get("/printer/objects/query?print_stats&display_status")["status"]
        acc = accuracy(_get("/server/history/list?limit=100&order=desc")["jobs"])
        k = acc["time"] or 1
        busy = None
        if ps["print_stats"].get("state") in ("printing", "paused"):
            prog = (ps.get("display_status") or {}).get("progress") or 0
            dur = ps["print_stats"].get("print_duration") or 0
            if prog > 0.02:
                busy = dt_now() + datetime.timedelta(seconds=dur * (1 - prog) / prog)
        rows = []
        for j in q:
            try:
                est = _get("/server/files/metadata?filename=" + urllib.parse.quote(j["filename"])).get("estimated_time") or 0
            except (OSError, ValueError, KeyError):
                est = 0
            rows.append({"filename": j["filename"], "seconds": est * k + (acc["prep_min"] or 0) * 60})
        plan = insights.plan(rows, dt_now(), busy)
        return {"busy_until": busy.isoformat(timespec="minutes") if busy else None, "quiet": insights.QUIET,
                "jobs": [{**r, "start": r["start"].isoformat(timespec="minutes"), "end": r["end"].isoformat(timespec="minutes")} for r in plan]}
    if kind == "spoolman":
        conf = printer_conf()
        url = (conf.get("spoolman") or "").rstrip("/")
        if not url:
            return {"spools": [], "note": "Spoolman isn't set up on this computer yet (its address goes in printer.json as \"spoolman\")."}
        with urllib.request.urlopen(url + "/api/v1/spool", timeout=8) as r:
            data = json.load(r)
        heads = conf.get("spool_heads") or {"T0": 1, "T1": 2, "T2": 3, "T3": 4}   # Legion d95efb set these spools up on 21 Nov
        return {"spools": insights.spools(data, heads), "url": url}
    raise KeyError(kind)


def dt_now():
    return datetime.datetime.now()


def accuracy(jobs):
    """Slicer against printer for finished jobs: per job the ratios, and medians to correct estimates with.
    jobs: Moonraker history entries. time = print_duration / slicer estimate; prep = total - print minutes (heating,
    homing, probing the slicer does not count); filament = printer mm / slicer mm (colour changes push it up)."""
    rows = []
    for j in jobs:
        m = j.get("metadata") or {}
        est, mm = m.get("estimated_time"), m.get("filament_total")
        if j.get("status") != "completed" or not est or not mm or not j.get("print_duration"):
            continue
        rows.append({"file": j["filename"], "t": j.get("end_time") or j.get("start_time"),
                     "time": round(j["print_duration"] / est, 3), "prep_min": round((j["total_duration"] - j["print_duration"]) / 60, 1),
                     "filament": round(j["filament_used"] / mm, 3), "colours": len(str(m.get("filament_type", "")).split(";"))})
    def median(xs):
        xs = sorted(xs)
        return xs[len(xs) // 2] if xs else None
    return {"jobs": rows, "time": median([r["time"] for r in rows]), "prep_min": median([r["prep_min"] for r in rows]),
            "filament": median([r["filament"] for r in rows]), "n": len(rows)}


def printer_conf():
    import printer
    return printer.load_conf()


def lan_ip():
    """This machine's address on the network (no packet is sent), so a link in a phone alert opens Workshop, not localhost."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))
        return s.getsockname()[0]
    except OSError:
        return "localhost"
    finally:
        s.close()


DAILY = os.path.join(os.path.expanduser("~"), ".config", "claude-3d-printing", "daily-check.json")


def daily_printer_check(now=None):
    """Once a day, when the printer is idle: copy its setup into --config-backup (git then shows what changed) and read
    klippy.log for faults; a phone push only when the log shows a fault since the last check. Returns what it did, or None."""
    import printer
    now = now or time.time()
    try:
        last = json.load(open(DAILY, encoding="utf-8"))
    except (OSError, ValueError):
        last = {}
    if ("t" in last and now - last["t"] < 20 * 3600) or printer.state() in printer.RUNNING:
        return None
    done = {"t": now}
    if CFG.get("config_backup"):
        r = printer.backup(CFG["config_backup"])
        done["changed"] = r["changed"]
        if r["changed"]:
            record("printer setup changed", ", ".join(r["changed"][:8]) + (" ..." if len(r["changed"]) > 8 else ""), who="daily check")
    path, found = printer.logcheck(3)
    faults = {k: v for k, v in found.items() if v["serious"]}
    key = sorted((k, v["last"]) for k, v in faults.items())
    done["faults"] = [list(x) for x in key]
    if faults and done["faults"] != last.get("faults"):   # the same old fault is not pushed every day
        record("klippy.log faults", "; ".join(f'{k} {v["count"]}x' for k, v in faults.items()), who="daily check")
        printer.push("Printer log shows a fault", "; ".join(f'{k} ({v["count"]}x): {v["last"][:90]}' for k, v in faults.items()), "warn",
                     click=CFG.get("url"))
    os.makedirs(os.path.dirname(DAILY), exist_ok=True)
    json.dump(done, open(DAILY, "w", encoding="utf-8"))
    return done


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


VIEW_GAP = 20   # mm between the nozzle and the top of the tallest thing on the plate when the bed comes up to the camera


def view_height(tallest=None, last_height=None):
    """The Z to raise the bed to so the plate is in the camera's view: the tallest thing on it (said by a person, else the
    last print's height) plus VIEW_GAP, never below 25 mm and never above 250 (the U1's Z runs 0 at the nozzle to 275 at
    the bottom). Raises ValueError for a height that does not make sense."""
    h = tallest if tallest is not None else (last_height or 0)
    h = float(h)
    if not 0 <= h <= 230:
        raise ValueError("the tallest thing on the plate is 0 to 230 mm")
    return round(min(250.0, max(25.0, h + VIEW_GAP)), 1)


LAST_Z = os.path.join(os.path.expanduser("~"), ".config", "claude-3d-printing", "last-z.json")


def remember_z(z=None):
    """Keep the bed's last known Z (from the printer while Z is homed, or a move Workshop just made). With the motors off
    the bed can only sink (Z grows), never rise, so the true Z is at or past this: a safe start for raising it without
    homing (Khalid, 22 Nov 2026). Unknown if someone winds the bed up by hand while the motors are off."""
    if z is None:
        try:
            with moonraker("GET", "/printer/objects/query?toolhead=homed_axes,position", timeout=5) as r:
                th = json.load(r)["result"]["status"]["toolhead"]
        except (OSError, ValueError, KeyError):
            return
        if "z" not in th.get("homed_axes", ""):
            return
        z = th["position"][2]
    try:
        os.makedirs(os.path.dirname(LAST_Z), exist_ok=True)
        json.dump({"z": round(float(z), 2), "t": time.time()}, open(LAST_Z, "w"))
    except OSError:
        pass


def known_z():
    try:
        return float(json.load(open(LAST_Z))["z"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def view_bed(tallest=None):
    """Bring the bed up to the camera (Khalid, 22 Nov 2026: Claude must always be able to see the plate). The U1 homes Z
    DOWN (endstop at 275 mm, the bottom), so homing moves the bed away from the nozzle and cannot crush a part; then the
    bed rises to view_height(), which stays VIEW_GAP above the tallest thing on the plate. Idle only; homes first when the
    printer is not homed (after a print the motors are off). Returns {"z", "homed"}."""
    with moonraker("GET", "/printer/objects/query?print_stats=state&toolhead=homed_axes", timeout=5) as r:
        st = json.load(r)["result"]["status"]
    if st["print_stats"]["state"] in ("printing", "paused"):
        raise PermissionError("a job is running; the bed is already where the print needs it")
    last = None
    try:
        with moonraker("GET", "/server/history/list?limit=1&order=desc", timeout=5) as r:
            last = json.load(r)["result"]["jobs"][0].get("metadata", {}).get("object_height")
    except (OSError, ValueError, KeyError, IndexError):
        pass
    if tallest is None and last is None:
        raise ValueError("no last print to size the gap from: say how tall the tallest thing on the plate is")
    z = view_height(tallest, last)
    homed = "z" in st["toolhead"].get("homed_axes", "")
    k = known_z()
    if not homed and k is not None and z < k:   # raise only, from a Z the bed is at or below: no homing, Z only
        for cmd in (f"SET_KINEMATIC_POSITION Z={k}", "G90", f"G1 Z{z} F600", "M400", "M84"):
            moonraker("POST", "/printer/gcode/script?script=" + urllib.parse.quote(cmd), timeout=120).read()
        remember_z(z)
        return {"z": z, "homed": False}
    if not homed:
        moonraker("POST", "/printer/gcode/script?script=G28", timeout=180).read()
    for cmd in ("G90", f"G1 Z{z} F900"):
        moonraker("POST", "/printer/gcode/script?script=" + urllib.parse.quote(cmd), timeout=120).read()
    remember_z(z)
    return {"z": z, "homed": not homed}


LIFT_MAX_PART = 190   # PRINT_END sends the bed to Z200 only when the last layer is under Z195


def lift_after_print(file, wait=120):
    """After a completed print, bring the bed up to the camera WITHOUT homing (Khalid, 22 Nov 2026: a full G28 to look at
    the plate is wrong). The U1's PRINT_END lowers the bed to Z200 and then M84 turns the motors off, so the printer no
    longer knows Z. The bed cannot rise by itself (it only sinks), so it is at Z200 or lower: tell the printer Z=200, rise
    to view_height(part), motors off again so any real move still homes first. Z only; X and Y never move. Completed
    prints only, parts up to LIFT_MAX_PART tall. Returns the Z it rose to, or None when it did nothing."""
    try:
        with moonraker("GET", "/server/files/metadata?filename=" + urllib.parse.quote(file or ""), timeout=10) as r:
            h = json.load(r)["result"].get("object_height")
    except (OSError, ValueError, KeyError):
        return None
    if h is None or not 0 < float(h) <= LIFT_MAX_PART:
        return None
    end = time.time() + wait
    while time.time() < end:   # PRINT_END's moves and M84 finish first
        with moonraker("GET", "/printer/objects/query?print_stats=state&idle_timeout=state&toolhead=homed_axes", timeout=5) as r:
            st = json.load(r)["result"]["status"]
        if st["print_stats"]["state"] != "complete":
            return None   # someone started something
        if st["idle_timeout"]["state"] != "Printing" and not st["toolhead"].get("homed_axes"):
            break
        time.sleep(3)
    else:
        return None
    remember_z(200)   # where PRINT_END parked it: the watcher's last reading is from the last layer, before the drop
    z = view_height(float(h))
    for cmd in ("SET_KINEMATIC_POSITION Z=200", "G90", f"G1 Z{z} F600", "M400", "M84"):
        moonraker("POST", "/printer/gcode/script?script=" + urllib.parse.quote(cmd), timeout=120).read()
    remember_z(z)
    record(f"bed up to the camera at Z{z} without homing", file, who="photo watch")
    return z


GALLERY = os.path.join(os.path.expanduser("~"), ".config", "claude-3d-printing", "gallery")


def gallery_skipped():
    """Prints someone asked not to photograph for the gallery (e.g. a personal piece with a name on it)."""
    try:
        return set(l.strip() for l in open(os.path.join(GALLERY, "skip.txt"), encoding="utf-8") if l.strip())
    except OSError:
        return set()


def gallery_snap(file, settle=3):
    """One photo of a finished part with the bed up at the camera, kept for the History gallery. Read-only on the printer.
    None for a print on the skip list."""
    import printer
    if os.path.basename(file or "") in gallery_skipped():
        return None
    time.sleep(settle)   # the bed has just stopped moving
    os.makedirs(GALLERY, exist_ok=True)
    name = time.strftime("%Y%m%d-%H%M%S-") + re.sub(r"[^\w-]", "_", os.path.splitext(os.path.basename(file or "print"))[0]) + ".jpg"
    printer.snap(os.path.join(GALLERY, name))
    with open(os.path.join(GALLERY, "gallery.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"t": time.time(), "file": file, "photo": name, "for": ""}) + "\n")
    return name


def gallery(photo=None, who_for=None):
    """The finished-part photos, newest first; photo + who_for tags who that part was for."""
    log = os.path.join(GALLERY, "gallery.jsonl")
    try:
        rows = [json.loads(l) for l in open(log, encoding="utf-8") if l.strip()]
    except (OSError, ValueError):
        rows = []
    if photo is not None:
        hit = next((r for r in rows if r["photo"] == photo), None)
        if not hit:
            raise ValueError("no such photo")
        hit["for"] = str(who_for or "")[:60]
        with open(log + ".part", "w", encoding="utf-8") as f:
            f.write("".join(json.dumps(r) + "\n" for r in rows))
        os.replace(log + ".part", log)
    return rows[::-1]


HEATERS = {"T0": "extruder", "T1": "extruder1", "T2": "extruder2", "T3": "extruder3", "bed": "heater_bed"}
LIMITS = {"x": (0.0, 271.0), "y": (0.0, 335.0)}
FANS = {"part": None, "cavity": "cavity_fan", "exhaust": "exhaust_fan", "circulation": "circulation_fan"}
CALIBRATIONS = {"shaper": ("SHAPER_CALIBRATE", "input shaping: the head shakes in X and Y for a few minutes"),
                "offsets": ("XYZ_OFFSET_CALIBRATE_ALL", "tool head offsets: each head touches the calibration point"),
                "mesh": ("BED_MESH_CALIBRATE", "bed mesh: the probe touches the whole bed"),
                "screws": ("AUTO_SCREWS_TILT_ADJUST", "bed tilt: probes the screw corners and says how far to turn each")}
RAW_NEVER = re.compile(r"^(SAVE_CONFIG|FIRMWARE_RESTART|RESTART|FORCE_MOVE|SET_KINEMATIC_POSITION|SET_STEPPER_ENABLE|STEPPER_BUZZ|"
                       r"Z_ENDSTOP_CALIBRATE|Z_OFFSET_APPLY|M500|M502)\b", re.I)


def control(req):
    """One printer action from the page or Claude, with the same rules for both. Returns (status, dict).
    Idle only (light and part fan excepted); moves need the printer homed and keep Z VIEW_GAP over the tallest thing."""
    act = str(req.get("action", ""))
    def st():
        with moonraker("GET", "/printer/objects/query?print_stats=state&toolhead=homed_axes,position", timeout=5) as r:
            return json.load(r)["result"]["status"]
    def g(cmd, timeout=60):
        moonraker("POST", "/printer/gcode/script?script=" + urllib.parse.quote(cmd), timeout=timeout).read()
    if act == "resume":   # the page's Resume and printer_resume: only when the printer will take it, else say why
        import printer
        with moonraker("GET", "/printer/objects/query?print_stats&pause_resume&machine_state_manager", timeout=5) as r:
            s0 = json.load(r)["result"]["status"]
        h = printer.hold(s0)
        if not h["can_resume"]:
            return 409, {"error": "Not resumed: " + h["waiting"] + ".", "why": h["why"]}
        if ((s0.get("print_stats") or {}).get("exception") or {}).get("id") == 532 and req.get("person_checked") is not True:
            return 409, {"error": "Not resumed: the printer's detector paused it. A person looks at the part (camera or in person) and "
                                  "says carry on first (person_checked).", "why": h["why"]}
        g("RESUME", timeout=120)
        return 200, {"done": "resumed" + (" after: " + h["why"] if h["why"] else "")}
    if act == "detector":   # the printer's own camera checks; saved on the printer, so it stays until changed back
        on, sens = req.get("spaghetti"), req.get("sensitivity")
        if on is None and sens is None:
            return 400, {"error": "give spaghetti (true or false) and/or sensitivity (low or high)"}
        if on is not None and not isinstance(on, bool):   # the string "false" is truthy: only a real true or false
            return 400, {"error": "spaghetti is true or false"}
        if sens is not None and sens not in ("low", "high"):
            return 400, {"error": "sensitivity is low or high"}
        cmd = "DEFECT_DETECTION_CONFIG" + (f" NOODLE_ENABLE={int(bool(on))}" if on is not None else "") + (f" NOODLE_SENSITIVITY={sens}" if sens else "")
        g(cmd)
        return 200, {"done": "spaghetti detector " + ", ".join(x for x in (("on" if on else "off") if on is not None else "", sens and sens + " sensitivity") if x)}
    s = st()
    busy = s["print_stats"]["state"] in ("printing", "paused")
    if act == "fan":
        fan, pct = str(req.get("fan", "part")), float(req.get("percent", -1))
        if fan not in FANS or not 0 <= pct <= 100:
            return 400, {"error": "fan is part, cavity, exhaust or circulation; percent 0 to 100"}
        if busy and fan != "part":
            return 409, {"error": "a job is running; only the part fan can change mid-print"}
        g(f"M106 S{round(pct * 2.55)}" if fan == "part" else f"SET_FAN_SPEED FAN={FANS[fan]} SPEED={pct / 100:.2f}")
        return 200, {"done": f"{fan} fan {pct:.0f}%"}
    if busy:
        return 409, {"error": "a job is running; that waits until it ends"}
    if act == "heat":
        h, c = str(req.get("heater")), float(req.get("celsius", -1))
        top = 110 if h == "bed" else 300
        if h not in HEATERS or not 0 <= c <= top:
            return 400, {"error": f"heater is T0 to T3 or bed; {h} takes 0 to {top} C"}
        g(f"SET_HEATER_TEMPERATURE HEATER={HEATERS[h]} TARGET={c:g}")
        return 200, {"done": f"{h} {'off' if not c else f'to {c:g} C'}"}
    if act == "move":
        if s["toolhead"].get("homed_axes") != "xyz":
            return 409, {"error": "not homed: bring the bed into view (it homes safely) or home first"}
        pos = dict(zip("xyz", s["toolhead"]["position"][:3]))
        want = {}
        for ax in "xyz":
            if req.get(ax) is not None:
                want[ax] = float(req[ax]) + (pos[ax] if req.get("relative") else 0)
        if not want:
            return 400, {"error": "give x, y and/or z"}
        for ax in ("x", "y"):
            if ax in want and not LIMITS[ax][0] <= want[ax] <= LIMITS[ax][1]:
                return 400, {"error": f"{ax.upper()} stays between {LIMITS[ax][0]:g} and {LIMITS[ax][1]:g} mm"}
        if "z" in want:
            tall = req.get("tallest_mm")
            if tall is None:
                try:
                    with moonraker("GET", "/server/history/list?limit=1&order=desc", timeout=5) as r:
                        tall = json.load(r)["result"]["jobs"][0].get("metadata", {}).get("object_height")
                except (OSError, ValueError, KeyError, IndexError):
                    tall = None
            if tall is None:
                return 400, {"error": "say how tall the tallest thing on the plate is (tallest_mm) before raising the bed"}
            try:
                tall = float(tall)
            except (TypeError, ValueError):
                tall = float("nan")
            if not 0 <= tall <= 230:   # a negative or NaN height took the 20 mm gap away (22 Nov 2026 review)
                return 400, {"error": "the tallest thing on the plate is 0 to 230 mm"}
            low = max(5.0, tall + VIEW_GAP)
            if not low <= want["z"] <= 275:
                return 400, {"error": f"Z stays between {low:g} and 275 mm: the nozzle keeps {VIEW_GAP} mm over {float(tall):g} mm on the plate"}
        g("G90")
        g("G1 " + " ".join(f"{ax.upper()}{v:.2f}" for ax, v in want.items()) + f" F{int(float(req.get('speed', 50)) * 60)}")
        return 200, {"done": "moved to " + ", ".join(f"{ax.upper()} {v:.1f}" for ax, v in want.items())}
    if act == "calibrate":
        k = str(req.get("which", ""))
        if k not in CALIBRATIONS:
            return 400, {"error": "which is " + ", ".join(CALIBRATIONS)}
        if req.get("nothing_in_the_way") is not True:
            return 400, {"error": "calibration moves the head and bed: confirm the plate is clear (nothing_in_the_way)"}
        if s["toolhead"].get("homed_axes") != "xyz":
            g("G28", timeout=180)
        g(CALIBRATIONS[k][0], timeout=900)
        return 200, {"done": f"{CALIBRATIONS[k][1]}: finished; the result is in the console"}
    if act == "gcode":
        cmd = str(req.get("command", "")).strip()
        if not cmd or "\n" in cmd or "\r" in cmd or len(cmd) > 200:
            return 400, {"error": "one command, under 200 characters"}
        if re.match(r"N\d", cmd, re.I):   # Klipper skips a leading line number, which hid SAVE_CONFIG from RAW_NEVER
            return 400, {"error": "no line numbers: send the command itself"}
        if RAW_NEVER.match(cmd):
            return 400, {"error": "that rewrites the printer's setup or forces a motor: do it on the printer itself"}
        g(cmd, timeout=600)
        return 200, {"done": f"sent {cmd}"}
    return 400, {"error": "action is heat, move, fan, calibrate, gcode, resume or detector"}


# What the pages and the MCP may reach on the printer through /moonraker/ (22 Nov 2026 review, P1: every other path,
# firmware_restart, machine/reboot, config uploads, went straight through, mid-print too). Method, path prefix.
PROXY_OK = {
    "GET": ("/printer/objects/query", "/server/job_queue/status", "/server/history/list", "/server/history/totals",
            "/server/files/list", "/server/files/metadata", "/server/files/gcodes/", "/server/files/timelapse/", "/server/gcode_store"),
    "POST": ("/printer/print/start", "/printer/print/pause", "/printer/print/cancel",   # resume only via /api/control (its checks)
             "/printer/gcode/script", "/server/job_queue/job", "/server/job_queue/start", "/server/job_queue/pause",
             "/server/files/upload"),
    "DELETE": ("/server/files/gcodes/", "/server/job_queue/job"),
}


def proxy_allowed(method, path):
    u = urllib.parse.urlparse(path)
    if ".." in urllib.parse.unquote(u.path) or not any(u.path == x or (x.endswith("/") and u.path.startswith(x)) or u.path.startswith(x + "?")
                                                         for x in PROXY_OK.get(method, ())):
        return False
    root = urllib.parse.parse_qs(u.query).get("root", ["gcodes"])
    if u.path in ("/server/files/list", "/server/files/upload"):   # never the printer's config folder
        return root in (["gcodes"], ["timelapse"]) if u.path.endswith("list") else root == ["gcodes"]
    return True


BODY_MAX = 300 * 1024 * 1024   # the largest request body Workshop reads (uploads are refused above 100 MB anyway)


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if CFG.get("verbose"):
            sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def send(self, code, body, ctype="application/json", extra=None):
        # A request refused before its body was read (403, oversize, 404) left that body on the kept-alive connection,
        # where it was parsed as the next request and could pass allowed() (22 Nov 2026 review, P0). Close instead.
        declared = int(self.headers.get("Content-Length") or 0) > 0 or "chunked" in (self.headers.get("Transfer-Encoding") or "").lower()
        if declared and not hasattr(self, "_body"):
            self.close_connection = True
            extra = dict(extra or {}, Connection="close")
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        gz = len(body) > 2048 and ctype.split(";")[0] in ("text/html", "application/json", "text/plain", "application/javascript", "image/svg+xml") \
            and "gzip" in (self.headers.get("Accept-Encoding") or "")
        if gz:   # the page is ~140 KB; a phone on the tailnet loads a quarter of that
            import gzip
            body = gzip.compress(body, 6)
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        if gz:
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        if not hasattr(self, "_body"):   # read once: guard() looks at it, proxy() forwards it
            if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
                # Caddy sometimes forwards an https request body in chunks with no Content-Length (22 Nov 2026); unread,
                # it stayed in the connection and was taken for the next request ("Unsupported method '{...}POST'")
                parts, total = [], 0
                while True:
                    try:   # a size line is short hex; "-1" or junk would slip under the cap (22 Nov 2026 review)
                        size = int(self.rfile.readline(64).split(b";")[0].strip() or b"0", 16)
                    except ValueError:
                        size = -1
                    total += max(size, 0)
                    if size < 0 or total > BODY_MAX:   # no endless chunked body filling the memory (22 Nov 2026 review)
                        self.close_connection = True
                        parts = []
                        break
                    if size == 0:
                        while self.rfile.readline(1024) not in (b"\r\n", b"\n", b""):   # trailers, then the blank line
                            pass
                        break
                    parts.append(self.rfile.read(size)); self.rfile.readline()
                self._body = b"".join(parts) or None
            else:
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    n = -1
                if n < 0 or n > BODY_MAX:   # never read it (a negative length reads to the end): the connection closes (see send)
                    self._body = None
                    self.close_connection = True
                else:
                    self._body = self.rfile.read(n) if n else None
        return self._body

    def proxy(self, path, timeout=30):
        hdrs = {k: v for k, v in self.headers.items() if k.lower() not in SKIP}
        try:
            r = moonraker(self.command, path, self.body(), hdrs, timeout)
        except urllib.error.HTTPError as e:
            r = e
        except OSError as e:
            return self.send(502, {"error": f"printer unreachable: {e}"})
        ctype = r.headers.get("Content-Type", "application/octet-stream")
        # a 206 without Content-Range is refused by iPhone Safari: the timelapse would neither play nor download (22 Nov)
        keep = {k: r.headers[k] for k in ("Content-Range", "Accept-Ranges", "Content-Disposition") if r.headers.get(k)}
        if ctype.startswith(("video/", "image/", "application/octet-stream")) and r.headers.get("Content-Length"):
            # stream big files as they arrive: the printer sends ~1.4 MB/s, so buffering a 58 MB timelapse kept Safari
            # waiting 40 s for the first byte and it gave up
            self.send_response(r.status)
            for k, v in {"Content-Type": ctype, "Content-Length": r.headers["Content-Length"], "Cache-Control": "no-store", **keep}.items():
                self.send_header(k, v)
            self.end_headers()
            try:
                shutil.copyfileobj(r, self.wfile, 256 * 1024)
            except (BrokenPipeError, ConnectionResetError):   # the viewer seeked or closed the video
                pass
            finally:
                r.close()   # free the printer's connection too (22 Nov 2026 review)
            return
        self.send(r.status, r.read(), ctype, keep)

    MCP_LOCAL_ONLY = ("printer_upload_model",)   # 22 Nov 2026 review: over HTTP it read files on alm-01

    def mcp(self):
        """The printer tools as an MCP server over HTTP (Streamable HTTP, stateless, JSON replies), so anyone's Claude
        (Claude Code, the desktop app) can add one address instead of installing printer_mcp.py (Khalid, 22 Nov 2026).
        The same 41 tools and the same safety checks: each call goes back through this server's own pages. The caller's
        name comes from the X-Workshop-User header or ?user= and every action is logged under it."""
        if self.command == "GET":
            return self.send(405, {"error": "POST JSON-RPC to /mcp"}, "application/json", {"Allow": "POST, DELETE"})
        if self.command == "DELETE":
            return self.send(200, {})
        import printer_mcp
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        who = re.sub(r"[^\w .'-]", "", self.headers.get("X-Workshop-User") or (q.get("user") or [""])[0])[:24].strip() or "someone"
        printer_mcp.URL = f"http://127.0.0.1:{self.server.server_address[1]}"
        printer_mcp._who.user = who
        try:
            msgs = json.loads(self.body() or b"null")
        except ValueError:
            return self.send(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
        batch = isinstance(msgs, list)
        replies = []
        for m in (msgs if batch else [msgs]):
            if not isinstance(m, dict):
                continue
            try:
                name = (m.get("params") or {}).get("name") if m.get("method") == "tools/call" else None
                if name in self.MCP_LOCAL_ONLY:   # it reads a path on the computer running the tools: over HTTP that is alm-01, not the caller's
                    res = {"content": [{"type": "text", "text": f"{name} reads a file on your own computer, so it only works in the "
                                                                "local printer_mcp.py. Over /mcp, put the model in the family designs "
                                                                "or slice it with printer_slice."}], "isError": True}
                else:
                    res = printer_mcp.handle(m)
                    if m.get("method") == "tools/list":
                        res = dict(res, tools=[t for t in res["tools"] if t["name"] not in self.MCP_LOCAL_ONLY])
                if m.get("id") is not None:
                    replies.append({"jsonrpc": "2.0", "id": m["id"], "result": res})
            except LookupError as e:
                replies.append({"jsonrpc": "2.0", "id": m.get("id"), "error": {"code": -32601, "message": f"unknown method {e}"}})
        printer_mcp._who.user = None
        if not replies:
            return self.send(202, b"", "application/json")
        return self.send(200, replies if batch else replies[0], "application/json")

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
        if urllib.parse.urlparse(self.path).path == "/mcp":
            # MCP clients (Claude Code, the desktop app) send no X-Workshop header. A web page cannot reach /mcp either:
            # it must send JSON, which forces a CORS preflight this server never grants, and any Origin is refused.
            return self.command in ("GET", "DELETE") or ((self.headers.get("Content-Type") or "").startswith("application/json")
                                                         and not self.headers.get("Origin"))
        if self.command != "GET":
            origin = self.headers.get("Origin")
            host_hdr = self.headers.get("Host", "")   # behind Caddy (workshop.almehdar.co.uk) the page is https
            if self.headers.get("X-Workshop") != "1" or (origin and origin not in ("http://" + host_hdr, "https://" + host_hdr)):
                return False
        return True

    def look(self):
        """A picture of a bench design from four sides in its colours, with its size (tools/look.py), as PNG.
        At most two drawings at once: each starts OpenSCAD, and a page full of images could start dozens."""
        if not _look_slots.acquire(blocking=False):
            return self.send(429, {"error": "busy drawing other designs; try again in a moment"})
        try:
            return self._look()
        finally:
            _look_slots.release()

    def _look(self):
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
        (the light is allowed), and nothing added to a queue that is running (it would start onto the last print).
        Moonraker also reads arguments from a request body and starts a file uploaded with print=true, so a body on a
        start or a G-code command is refused, an upload that would start itself is refused, and no start goes ahead while
        the last print is still on the bed (independent review, 21 Nov 2026)."""
        if self.command == "POST" and p.startswith(("/printer/gcode/script", "/printer/print/start", "/server/job_queue/start")) \
                and self.body():
            return "send the command in the address, not in a body"
        if self.command == "POST" and p.startswith("/server/files/upload"):
            if "print=true" in p.lower() or UPLOAD_PRINT.search(self.body() or b""):
                return "upload the file, then start it from Files or the queue, after the bed check"
        if self.command == "POST" and p.startswith(("/printer/print/start", "/server/job_queue/start")) and bed().get("state") == "part waiting":
            return "the last print is still on the bed: mark it clear first (Took it off in Workshop, or tell Claude it is clear)"
        if self.command == "POST" and p.startswith("/printer/gcode/script"):
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(p).query, keep_blank_values=True)
            if len(qs.get("script", [])) != 1 or set(qs) - {"script"}:
                return "one G-code command, as ?script=... and nothing else"   # ?xscript=&script= slipped past (review P1)
            script = qs["script"][0]
            if re.match(r"\s*N\d", script, re.I):
                return "no line numbers: send the command itself"   # Klipper skips a leading N1, which hid SAVE_CONFIG
            if "\n" in script or "\r" in script:
                return "one G-code command at a time"
            speed = re.fullmatch(r"M220 S(\d{1,3})", script.strip().upper())
            if speed and not 50 <= int(speed.group(1)) <= 150:
                return "speed is 50 to 150 percent"
            pause_at = re.fullmatch(r"SET_PAUSE_AT_LAYER (LAYER=\d{1,4}|ENABLE=0)", script.strip().upper())   # never MACRO=: it would run any command
            if RAW_NEVER.match(script.strip()):
                return "that rewrites the printer's setup or forces a motor: do it on the printer itself"
            if script.strip().upper().startswith("SET_PAUSE_AT_LAYER") and not pause_at:
                return "pause at layer takes LAYER=<number> or ENABLE=0 only"
            if not (script.upper().startswith("SET_LED ") or speed or pause_at) and self.printer_busy():   # light, speed and pause-at-layer are fine mid-print
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
        # one handler serves every request on a kept-alive connection (Caddy reuses them): forget the last request's
        # body, or the next request reads it as its own (22 Nov 2026: replies shifted one request behind)
        self.__dict__.pop("_body", None)
        p = self.path
        if not self.allowed():
            return self.send(403, {"error": "refused: open Workshop by its address, not from another site"})
        if p in ("/icon-180.png", "/icon-512.png"):   # home-screen icon (phones: Share > Add to Home Screen)
            return self.send(200, open(os.path.join(TOOLS, "workshop" + p.replace("/icon", "-icon")), "rb").read(), "image/png")
        if p == "/manifest.webmanifest":
            return self.send(200, {"name": "Workshop", "short_name": "Workshop", "start_url": "/#now", "display": "standalone",
                                   "background_color": "#111114", "theme_color": "#111114",
                                   "icons": [{"src": "/icon-180.png", "sizes": "180x180", "type": "image/png"},
                                             {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"}]},
                             "application/manifest+json")
        if urllib.parse.urlparse(p).path == "/mcp":
            return self.mcp()
        if urllib.parse.urlparse(p).path in ("/", "/index.html"):
            return self.send(200, open(os.path.join(TOOLS, "workshop.html"), "rb").read(), "text/html; charset=utf-8",
                             {"Cache-Control": "no-cache"})   # an updated page shows on the next load, not a cached copy
        if p.startswith("/moonraker/"):
            path = p[len("/moonraker"):]
            if not proxy_allowed(self.command, path):
                return self.send(403, {"error": "not available through Workshop: use the printer itself for that"})
            why = self.guard(path)
            if why:
                return self.send(409, {"error": why})
            if self.command == "POST" and path.startswith("/server/job_queue/start"):
                # start ONE job and never let the queue run, so every later job needs its own bed check. Starting the queue
                # and pausing it straight after started nothing (22 Nov 2026: Moonraker loads the job after the pause
                # lands): take the first job off the queue and start that file directly; the queue stays held throughout.
                try:
                    with moonraker("GET", "/server/job_queue/status", timeout=10) as r:
                        jobs = json.load(r)["result"]["queued_jobs"]
                    if not jobs:
                        return self.send(409, {"error": "the queue is empty"})
                    job = jobs[0]
                    moonraker("DELETE", "/server/job_queue/job?job_ids=" + urllib.parse.quote(job["job_id"]), timeout=10).read()
                    moonraker("POST", "/printer/print/start?filename=" + urllib.parse.quote(job["filename"]), timeout=30).read()
                except (OSError, ValueError, KeyError) as e:
                    return self.send(502, {"error": f"could not start the next job: {e}"})
                record("started the next queued job", job["filename"], who=actor(self))
                return self.send(200, {"result": "ok", "started": job["filename"]})
            what = self.describe(path)
            if what:
                record(what[0], what[1], who=actor(self))
            return self.proxy(path, timeout=600 if ("upload" in path or "gcode/script" in path) else 30)
        if p.startswith("/screen.jpg"):   # the printer's touchscreen as a still (Extended Firmware lists it as a webcam)
            try:
                cams = _get("/server/webcams/list", timeout=5)["webcams"]
                snap = next((c.get("snapshot_url") for c in cams if c.get("service") == "iframe" and "/screen" in (c.get("stream_url") or "")), None)
                if not snap:
                    return self.send(404, {"error": "this printer has no remote screen"})
                url = snap if snap.startswith("http") else f"http://{CFG['printer']}{snap}"
                with urllib.request.urlopen(url, timeout=10) as r:
                    return self.send(200, r.read(), r.headers.get("Content-Type", "image/jpeg"))
            except (OSError, ValueError, KeyError) as e:
                return self.send(502, {"error": f"screen: {e}"})
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
        if p == "/api/boot":   # cheap: the page asks every 30 s and reloads when Workshop restarted
            return self.send(200, {"boot": BOOT})
        if p == "/api/info":
            import orca
            try:
                orca.orca_cli()
                slicer = True
            except SystemExit:
                slicer = False
            live = screen = None
            try:   # the printer's own webcam list names the live player (U1 Extended Firmware: /webcam/webrtc) and the screen
                with moonraker("GET", "/server/webcams/list", timeout=3) as r:
                    cams = json.load(r)["result"]["webcams"]
                host = f"http://{CFG['printer']}"
                for c in cams:
                    url = c.get("stream_url") or ""
                    full = url if url.startswith("http") else host + url if url.startswith("/") else None
                    if not full:
                        continue
                    if c.get("service", "").startswith("webrtc") and not live:
                        live = full
                    elif c.get("service") == "iframe" and "/screen" in url:
                        screen = full
            except (OSError, ValueError, KeyError):
                pass
            conf = printer_conf()
            push = (f'{conf.get("ntfy_server", "https://ntfy.sh").rstrip("/")}/{conf["ntfy_topic"]}' if conf.get("ntfy_topic") else None)
            return self.send(200, {"printer": CFG["printer"], "profile": CFG["profile"], "slicer": slicer, "live_camera": live, "live_relay": CFG.get("live_relay"), "printer_screen": screen, "push": push, "boot": BOOT,
                                   "presets": getattr(orca, "PRESET_WORDS", {}), "prices": filament_prices(CFG.get("register"))})
        m = re.fullmatch(r"/gallery-photo/([\w-]+\.jpg)", p)
        if m:
            f = os.path.join(GALLERY, m.group(1))
            return self.send(200, open(f, "rb").read(), "image/jpeg") if os.path.isfile(f) else self.send(404, {"error": "no such photo"})
        if p == "/api/gallery":   # GET the finished-part photos; POST {"photo", "for"} says who a part was for
            try:
                if self.command == "POST" and isinstance(json.loads(self.body() or b"{}"), dict) and json.loads(self.body() or b"{}").get("skip"):
                    f = os.path.basename(str(json.loads(self.body())["skip"]))[:200]   # {"skip": "file.gcode"}: no gallery photo of that print
                    os.makedirs(GALLERY, exist_ok=True)
                    if f not in gallery_skipped():
                        open(os.path.join(GALLERY, "skip.txt"), "a", encoding="utf-8").write(f + "\n")
                    record("no gallery photo for this print", f, who=actor(self))
                    rows = gallery()
                elif self.command == "POST":
                    r = json.loads(self.body() or b"{}")
                    rows = gallery(str(r.get("photo", "")), r.get("for"))
                    record(f'tagged a finished part as for {str(r.get("for") or "nobody")[:60]}', str(r.get("photo", "")), who=actor(self))
                else:
                    rows = gallery()
            except (ValueError, AttributeError) as e:
                return self.send(400, {"error": str(e)})
            return self.send(200, rows)
        m = re.fullmatch(r"/calibration-photo/([\w-]+\.jpg)", p)
        if m:
            f = os.path.join(CAL_PHOTOS, m.group(1))
            return self.send(200, open(f, "rb").read(), "image/jpeg") if os.path.isfile(f) else self.send(404, {"error": "no such photo"})
        if p == "/api/requests":
            if self.command == "POST":
                try:
                    r = json.loads(self.body() or b"{}")
                    return self.send(201, requests_(r if isinstance(r, dict) else {}, who=actor(self))[0] if not (isinstance(r, dict) and r.get("id")) else {"ok": True})
                except (ValueError, TypeError) as e:
                    return self.send(400, {"error": str(e)})
            return self.send(200, requests_())
        if p == "/api/parts":   # cancel one part: GET lists them, POST {name} cancels that one only (after a person's yes)
            try:
                if self.command == "POST":
                    r = json.loads(self.body() or b"{}")
                    return self.send(*cancel_part(str(r.get("name", "")), actor(self)))
                return self.send(200, parts())
            except (OSError, ValueError, KeyError) as e:
                return self.send(502, {"error": f"could not read the parts: {e}"})
        if p.startswith("/parts-map.png"):   # the bed from above: part outlines, ?mark=NAME filled red, layer 1 in blue
            q = urllib.parse.parse_qs(urllib.parse.urlparse(p).query)
            try:
                return self.send(200, parts_png((q.get("mark") or [None])[0]), "image/png", {"Cache-Control": "no-store"})
            except (OSError, ValueError, KeyError) as e:
                return self.send(502, {"error": str(e)})
        if p == "/api/bed":
            if self.command == "POST":   # "I took it off": the bed is clear again, and who did it is logged
                b = bed()
                bed("clear", who=actor(self), file=b.get("file", ""))
                record("took the part off the bed", b.get("file", ""), who=actor(self))
            return self.send(200, bed())
        if p == "/api/plate":
            if self.command == "POST":   # {"plate": "smooth" | "textured"}: who swapped it is logged
                try:
                    name = str(json.loads(self.body() or b"{}").get("plate", ""))
                except (ValueError, AttributeError):
                    name = ""
                if name not in PLATES:
                    return self.send(400, {"error": "plate is textured or smooth"})
                plate(name, who=actor(self))
                record(f"put the {name} plate on", "", who=actor(self))
            return self.send(200, plate())
        if p == "/api/maintenance":   # GET the list; POST {"task"} marks one done (who did it is logged)
            try:
                task = str(json.loads(self.body() or b"{}").get("task", "")) if self.command == "POST" else None
                rows = maintenance(task, who=actor(self))
            except (OSError, ValueError, KeyError, AttributeError) as e:
                return self.send(400 if isinstance(e, ValueError) else 502, {"error": str(e)})
            if task:
                record("did maintenance: " + MAINT_TASKS[task][0].split(":")[0].lower(), "", who=actor(self))
            return self.send(200, rows)
        if p.startswith("/api/filament-check"):   # before a start: would any spool run out mid-print?
            f = (urllib.parse.parse_qs(urllib.parse.urlparse(p).query).get("file") or [""])[0]
            try:
                meta = _get("/server/files/metadata?filename=" + urllib.parse.quote(f), timeout=10)
            except (OSError, ValueError, KeyError) as e:
                return self.send(502, {"error": f"could not read {f}: {e}"})
            return self.send(200, {"file": f, "short": filament_short(meta)})
        if p == "/api/spools":
            if self.command == "POST":   # a new spool on a head: {head, spool, grams}
                try:
                    r = json.loads(self.body() or b"{}")
                    head = str(r.get("head", "")).upper()
                    if not re.fullmatch(r"T[0-3]", head):
                        raise ValueError("head is T0 to T3")
                    grams = float(r.get("grams") or 1000)
                    if not 10 <= grams <= 5000:
                        raise ValueError("grams is 10 to 5000")
                    data = spools()
                    data[head] = {"spool": str(r.get("spool", "")).strip()[:60] or "spool", "start_g": grams, "used_g": 0, "since": time.time()}
                    spools(data)
                    record(f"new spool on {head}: {data[head]['spool']}, {grams:.0f} g", "", who=actor(self))
                    return self.send(201, data[head])
                except ValueError as e:
                    return self.send(400, {"error": str(e)})
            return self.send(200, {"low_g": LOW_G, "heads": spools()})
        if p == "/api/timelapses":   # the printer's own videos (Extended Firmware makes one per print), newest first
            try:
                files = _get("/server/files/list?root=timelapse")
                names = {f["path"] for f in files}
                vids = sorted((f for f in files if f["path"].lower().endswith(".mp4")), key=lambda f: -f["modified"])
                return self.send(200, [{"file": f["path"], "size": f["size"], "t": f["modified"],
                                        "cover": f["path"][:-4] + "_cover.jpg" if f["path"][:-4] + "_cover.jpg" in names else None,
                                        "print": re.sub(r"_\d{14}$", "", f["path"][:-4])} for f in vids[:40]])
            except (OSError, ValueError, KeyError) as e:
                return self.send(502, {"error": str(e)})
        if p in ("/api/health", "/api/costs", "/api/plan", "/api/spoolman"):   # read-only views built by tools/insights.py
            try:
                return self.send(200, insight(p.rsplit("/", 1)[1]))
            except (OSError, ValueError, KeyError) as e:
                return self.send(502, {"error": f"could not read it: {e}"})
        if p == "/api/accuracy":   # slicer vs printer from the printer's own history
            try:
                with moonraker("GET", "/server/history/list?limit=100&order=desc", timeout=15) as r:
                    return self.send(200, accuracy(json.load(r)["result"]["jobs"]))
            except (OSError, ValueError, KeyError) as e:
                return self.send(502, {"error": str(e)})
        if p == "/api/doctor":   # the print doctor's reports, newest first
            rows = []
            try:
                for f in sorted(os.listdir(DOCTOR), reverse=True)[:10]:
                    rows.append(json.load(open(os.path.join(DOCTOR, f), encoding="utf-8")))
            except (OSError, ValueError):
                pass
            return self.send(200, {"reports": rows})
        if p.startswith("/api/claude-usage"):   # the weekly email's spend count: photo checks and doctor reports in the last N days
            q = urllib.parse.parse_qs(urllib.parse.urlparse(p).query)
            since, tok = time.time() - 86400 * min(31, int(q.get("days", ["7"])[0] or 7)), {"checks": 0, "input_tokens": 0, "output_tokens": 0}
            logs = [PHOTO_LOG] + [os.path.join(DOCTOR, f) for f in (os.listdir(DOCTOR) if os.path.isdir(DOCTOR) else [])]
            for path in logs:
                try:
                    lines = open(path, encoding="utf-8").read().splitlines() if path == PHOTO_LOG else [open(path, encoding="utf-8").read().replace("\n", " ")]
                except OSError:
                    continue
                for x in lines:
                    try:
                        d = json.loads(x)
                    except ValueError:
                        continue
                    if (d.get("t") or 0) >= since and d.get("usage"):
                        tok["checks"] += 1
                        tok["input_tokens"] += d["usage"].get("input_tokens") or 0
                        tok["output_tokens"] += d["usage"].get("output_tokens") or 0
            return self.send(200, {"model": PHOTO_MODEL, **tok})
        if p == "/api/photo-checks":   # the photo watch's recent verdicts, newest first (no photos, no keys)
            rows = []
            try:
                for x in open(PHOTO_LOG, encoding="utf-8").read().splitlines()[-30:][::-1]:
                    try:
                        d = json.loads(x)
                        rows.append({k: d.get(k) for k in ("t", "file", "layer", "verdict", "problem", "confidence", "reason", "error", "second_look", "final", "quality", "flaws", "advice", "usage")})
                    except ValueError:
                        pass
            except OSError:
                pass
            return self.send(200, {"enabled": bool(api_keys()), "every": PHOTO_EVERY, "checks": rows})
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
        if p == "/api/control" and self.command == "POST":   # the Printer tab's panel and Claude's tools: one set of rules
            try:
                req = json.loads(self.body() or b"{}")
                code, out = control(req if isinstance(req, dict) else {})
            except (ValueError, TypeError) as e:
                return self.send(400, {"error": str(e)})
            except OSError as e:
                return self.send(502, {"error": f"printer: {e}"})
            if code == 200:
                record(out["done"], req.get("action", ""), who=actor(self))
            return self.send(code, out)
        if p == "/api/view-bed" and self.command == "POST":   # bed up to the camera, gap kept above the tallest thing on it
            try:
                req = json.loads(self.body() or b"{}")
                tallest = req.get("tallest_mm") if isinstance(req, dict) else None
                v = view_bed(None if tallest is None else float(tallest))
            except PermissionError as e:
                return self.send(409, {"error": str(e)})
            except (ValueError, TypeError) as e:
                return self.send(400, {"error": str(e)})
            except OSError as e:
                return self.send(502, {"error": f"printer: {e}"})
            record("brought the bed up to the camera", f"Z {v['z']} mm" + (", homed first" if v["homed"] else ""), who=actor(self))
            return self.send(200, v)
        if p in ("/api/unload", "/api/load") and self.command == "POST":
            try:
                req = json.loads(self.body() or b"{}")
                head = str(req.get("head", "")) if isinstance(req, dict) else ""
            except ValueError:
                head = ""
            if not re.fullmatch(r"all|T?[0-3]", head, re.I):
                return self.send(400, {"error": "head is T0 to T3, or all"})
            action = p.rsplit("/", 1)[1]
            if self.printer_busy():
                return self.send(409, {"error": f"a job is running; {action} after it ends"})
            if not start_unload(head, action):
                return self.send(409, {"error": "a load or unload is already running; wait for it to finish"})
            record(action, head, who=actor(self))
            return self.send(202, {"started": head})
        if p.startswith("/api/upload-model") and self.command == "POST":   # ?name=part.stl&colour=1c1c1c&preset=lean&queue=1, body = the file
            q = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(p).query).items()}
            n = int(self.headers.get("Content-Length") or 0)
            if n > MODEL_MAX:
                return self.send(400, {"error": "the file is bigger than 100 MB"})
            data = self.body() or b""
            req = {"preset": q.get("preset") or "lean", "colour": q.get("colour"), "queue": q.get("queue") == "1",
                   "upload": True, "allow_substitute": q.get("allow_substitute") == "1", "glue": q.get("glue") == "1",
                   "set": urllib.parse.parse_qs(urllib.parse.urlparse(p).query).get("set", [])}   # ?set=layer=0.08, filtered by SLICE_SETS
            return self.send(*slice_upload(q.get("name", ""), data, req))
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
    ap.add_argument("--live-relay", help="same-origin path of a live-video relay page (e.g. go2rtc behind Caddy: /relay/stream.html?src=u1); "
                    "pages opened by the https name use it instead of the printer's own WebRTC, which serves one viewer")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--register", help="a filament register (markdown table with Material and a price per kg in pounds) for print costs")
    ap.add_argument("--config-backup", help="a folder (e.g. almehdar's printer/u1-config) the printer's setup is copied into once a day while idle")
    ap.add_argument("--print-log", help="a print register (markdown table starting '| Date |', like registers/prints.md) to add a row to when a print ends")
    ap.add_argument("--preview", action="store_true", help="the page and API only: no watcher (photos, photo watch, print log, spools), so a second copy can be looked at safely")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    if not a.printer:
        sys.exit("give --printer <address> (or set PRINTER_HOST)")
    benches = [os.path.realpath(os.path.expanduser(b)) for b in (a.bench or [os.path.join(REPO, "examples", "bench.json")])]
    CFG.update(config_backup=a.config_backup and os.path.expanduser(a.config_backup), print_log=a.print_log and os.path.expanduser(a.print_log), register=a.register and os.path.expanduser(a.register), url=f"http://{a.bind if a.bind != '0.0.0.0' else lan_ip()}:{a.port}/", printer=a.printer, moonraker=f"http://{a.printer}:{a.moonraker_port}", camera=a.camera, benches=benches,
               profile=a.profile, verbose=a.verbose, allow_hosts=[h.lower() for h in a.allow_host], live_relay=a.live_relay, bed=json.load(open(benches[0], encoding="utf-8")).get("bed", 256))
    import threading
    if not a.preview:   # the watcher takes photos, pauses on failures and writes the print log: one copy only
        threading.Thread(target=watch_alerts, daemon=True).start()   # the console scrolls away in minutes; keep warnings
    class Server(http.server.ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            # a phone that closes a page or a video mid-download is normal: keep the log for real errors
            if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError, TimeoutError)):
                return
            super().handle_error(request, client_address)
    srv = Server((a.bind, a.port), Handler)
    print(f"Workshop on http://{a.bind}:{a.port} for printer {a.printer} (Ctrl+C stops)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
