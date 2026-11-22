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


def slice_parts(d, parts, req):
    import make_3mf
    import orca
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
        if len(parts) == 1 and parts[0][1].lower().endswith(".3mf"):   # an uploaded 3MF keeps its own parts and heads
            p3 = parts[0][1]
        else:
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
                alert_change(prev, s, was)
            prev = s["state"]
            if s["state"] == "printing" and s["layer"] and (s["file"], s["layer"]) != last:
                f = printer.layer_photo(s["file"], s["layer"].split("/")[0])
                last = (s["file"], s["layer"])
                was = os.path.dirname(f)
                n = int(s["layer"].split("/")[0] or 0)
                if s["file"] not in checked:
                    checked[s["file"]] = checked_block(s["file"])
                due = (n == 2 and checked[s["file"]] < 0) or (n > 2 and n // PHOTO_EVERY > checked[s["file"]])
                if due and api_key() and not (watcher and watcher.is_alive()):
                    first = checked[s["file"]] < 0   # the first look at this print carries the first-layer plan, whatever layer it lands on
                    checked[s["file"]] = n // PHOTO_EVERY
                    watcher = threading.Thread(target=photo_watch, args=(s["file"], n, f, first), daemon=True)
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
    msg = {"paused": (f"Print paused: {f}", (s.get("message") or "Check the printer before resuming.").strip(), "warn"),
           "complete": (f"Print finished: {f}", f"Took {took}. Let the bed cool before taking it off." if took else "Let the bed cool before taking it off.", "good"),
           "cancelled": (f"Print cancelled: {f}", took and f"After {took}.", "bad"),
           "error": (f"Printer error: {f}", (s.get("message") or "Look at the printer.").strip(), "bad")}.get(s["state"])
    if msg and prev in ("printing", "paused"):
        printer.push(*msg, click=CFG.get("url"))
        if s["state"] in ("complete", "cancelled", "error"):   # a part (or what's left of one) now sits on the bed
            bed("part waiting", who="", file=s.get("file") or "")
        if s["state"] == "complete":   # Claude looks at what came out (Khalid, 22 Nov: Claude does the jobs without being asked)
            import threading
            threading.Thread(target=finish_look, args=(s.get("file") or "",), daemon=True).start()
        if s["state"] in ("complete", "cancelled", "error"):   # what it really used comes off the spools
            try:
                with moonraker("GET", "/server/files/metadata?filename=" + urllib.parse.quote(s.get("file") or ""), timeout=10) as r:
                    meta = json.load(r)["result"]
                use = take_off_spools(s.get("file"), (s.get("filament_used_m") or 0) * 1000, meta)
                log_print(s, meta, use, folder)
            except (OSError, ValueError, KeyError) as e:
                print("spool update or print log failed:", e, flush=True)
    elif s["state"] == "printing" and prev not in ("printing", "paused"):
        bed("printing", who="", file=f)
    if s["state"] == "printing" and prev == "paused":
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
            if os.name == "nt" or not os.stat(f).st_mode & 0o077:   # Windows reports 0o666 whatever the ACL says; the profile folder is per user
                text = open(f, encoding="utf-8").read().strip()   # the bare key, or put-key's ANTHROPIC_API_KEY=... line
                keys.append(text.split("=", 1)[1].strip() if text.startswith("ANTHROPIC_API_KEY=") else text)
        except OSError:
            pass
    return [k for k in dict.fromkeys(keys) if k]


def api_key():
    return (api_keys() or [None])[0]


FIRST_LAYER_TEXT = """
The second picture is a top-down drawing of what the slicer planned for the first layer (blue lines, front edge at the
bottom, 50 mm grid). Compare it with the photo: is there plastic on the plate where the plan has it, and has anything
been dragged, lifted or left out? A first layer is thin and the camera sees it edge-on, so only call detached when a part
or line has clearly lifted, moved or is missing; anything you cannot see clearly is ok with lower confidence."""


def look_at_photo(jpg, layer, plan=None, prompt=None, schema=None):
    """One Claude API call: {verdict, problem, confidence, reason} for a camera frame, or raises OSError/ValueError.
    plan: PNG bytes of the first layer's planned paths (the first-layer check), sent as a second picture.
    prompt and schema replace the in-print check (the finished-print look uses its own)."""
    import base64
    keys = api_keys()
    if not keys:
        raise ValueError("no API key")
    body = {"model": PHOTO_MODEL, "max_tokens": 2000, "fallbacks": "default",
            "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": schema or PHOTO_SCHEMA}},
            "messages": [{"role": "user", "content": [
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
        printer.push(f"Claude's look: {v['quality']}" + (f", {flaws}" if flaws else ""), f"{os.path.basename(file or '')}. {v['reason']} {v['advice']}".strip(),
                     {"good": "good", "flaws": "warn", "failed": "bad"}[v["quality"]], click=CFG.get("url"))
    except (OSError, ValueError, KeyError, StopIteration) as e:
        row["error"] = "finished-print look: " + str(e)[:180]
    with open(PHOTO_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return row


def checked_block(file):
    """The highest 25-layer block already checked for this file, from the log, so a Workshop restart does not check again."""
    best = -1
    try:
        for x in open(PHOTO_LOG, encoding="utf-8").read().splitlines()[-200:]:
            try:
                d = json.loads(x)
                if d.get("file") == file and d.get("layer") is not None:
                    best = max(best, int(d["layer"]) // PHOTO_EVERY)
            except (ValueError, TypeError):
                pass
    except OSError:
        pass
    return best


def photo_watch(file, layer, photo, first=None):
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
    try:
        v = look_at_photo(photo, layer, plan)
        log({"photo": photo, "first_layer": bool(plan), **v})
        if not (v["verdict"] == "pause" and v["problem"] in PAUSE_ON and v["confidence"] >= 0.85):
            return
        time.sleep(8)   # a second, fresh frame must agree before Claude pauses anything on its own
        second = os.path.join(os.path.dirname(photo), time.strftime("%Y%m%d-%H%M%S") + f"-check{layer}.jpg")
        printer.snap(second)
        v2 = look_at_photo(second, layer, plan)
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
    sp, prices = spools(), filament_prices(CFG.get("register"))
    heads, cost = [], 0.0
    for h, g in use.items():
        i = int(h[1:])
        mat = types[i] if i < len(types) else ""
        heads.append(f'{(sp.get(h) or {}).get("spool") or mat or "?"} on {h}')
        cost += g * prices.get(mat.upper(), 0) / 1000
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
    if not homed:
        moonraker("POST", "/printer/gcode/script?script=G28", timeout=180).read()
    for cmd in ("G90", f"G1 Z{z} F900"):
        moonraker("POST", "/printer/gcode/script?script=" + urllib.parse.quote(cmd), timeout=120).read()
    return {"z": z, "homed": not homed}


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
        if not hasattr(self, "_body"):   # read once: guard() looks at it, proxy() forwards it
            n = int(self.headers.get("Content-Length") or 0)
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
            script = urllib.parse.unquote_plus(p.split("script=", 1)[-1]) if "script=" in p else ""
            if "\n" in script or "\r" in script:
                return "one G-code command at a time"
            speed = re.fullmatch(r"M220 S(\d{1,3})", script.strip().upper())
            if speed and not 50 <= int(speed.group(1)) <= 150:
                return "speed is 50 to 150 percent"
            pause_at = re.fullmatch(r"SET_PAUSE_AT_LAYER (LAYER=\d{1,4}|ENABLE=0)", script.strip().upper())   # never MACRO=: it would run any command
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
        if p in ("/", "/index.html"):
            return self.send(200, open(os.path.join(TOOLS, "workshop.html"), "rb").read(), "text/html; charset=utf-8",
                             {"Cache-Control": "no-cache"})   # an updated page shows on the next load, not a cached copy
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
                except OSError as e:   # the queue would start the next job onto this one's part: say so loudly
                    record("could not hold the queue after starting it", str(e), who="Workshop")
                    try:
                        import printer
                        printer.push("Queue not held", "The next queued job would start when this one ends, onto the finished "
                                     "part. Hold the queue in Workshop.", "bad", click=CFG.get("url"))
                    except Exception:
                        pass
                return
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
            return self.send(200, {"printer": CFG["printer"], "profile": CFG["profile"], "slicer": slicer, "live_camera": live, "printer_screen": screen, "push": push, "boot": BOOT,
                                   "presets": getattr(orca, "PRESET_WORDS", {}), "prices": filament_prices(CFG.get("register"))})
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
        if p == "/api/photo-checks":   # the photo watch's recent verdicts, newest first (no photos, no keys)
            rows = []
            try:
                for x in open(PHOTO_LOG, encoding="utf-8").read().splitlines()[-30:][::-1]:
                    try:
                        d = json.loads(x)
                        rows.append({k: d.get(k) for k in ("t", "file", "layer", "verdict", "problem", "confidence", "reason", "error", "second_look", "final", "quality", "flaws", "advice")})
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
                   "upload": True, "allow_substitute": q.get("allow_substitute") == "1"}
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
               profile=a.profile, verbose=a.verbose, allow_hosts=[h.lower() for h in a.allow_host], bed=json.load(open(benches[0], encoding="utf-8")).get("bed", 256))
    import threading
    if not a.preview:   # the watcher takes photos, pauses on failures and writes the print log: one copy only
        threading.Thread(target=watch_alerts, daemon=True).start()   # the console scrolls away in minutes; keep warnings
    srv = http.server.ThreadingHTTPServer((a.bind, a.port), Handler)
    print(f"Workshop on http://{a.bind}:{a.port} for printer {a.printer} (Ctrl+C stops)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
