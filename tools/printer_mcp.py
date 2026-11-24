"""Printer tools for the Claude desktop app (and Claude Code): an MCP server that drives a printer through Workshop.

Each person runs it in their own Claude app, so their own Claude account does the work, and every action goes
through Workshop's safety checks and lands in its activity log under their name.

    WORKSHOP_URL=http://192.168.1.50:8090 WORKSHOP_USER=Khalid python printer_mcp.py

Claude desktop app, Settings > Developer > Edit Config (claude_desktop_config.json):
    {"mcpServers": {"printer": {"command": "python3", "args": ["/path/to/claude-3d-printing/tools/printer_mcp.py"],
                                "env": {"WORKSHOP_URL": "http://192.168.1.50:8090", "WORKSHOP_USER": "Khalid"}}}}
Claude Code:  claude mcp add printer -e WORKSHOP_URL=... -e WORKSHOP_USER=Khalid -- python3 /path/to/tools/printer_mcp.py

Speaks MCP over stdio (JSON-RPC, one message per line). Standard library only; Python 3.8 or later.
"""
import base64
import json
import threading
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

URL = os.environ.get("WORKSHOP_URL", "http://127.0.0.1:8090").rstrip("/")
USER = os.environ.get("WORKSHOP_USER", "someone")
_who = threading.local()   # Workshop's /mcp sets the caller per request (many people at once); stdio uses USER
VERSION = "1.0.0"


def http(method, path, body=None, raw=False, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"X-Workshop": "1", "X-Workshop-User": getattr(_who, "user", None) or USER}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(URL + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = r.read()
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read() or b"{}").get("error")
            msg = msg.get("message") if isinstance(msg, dict) else msg
        except ValueError:
            msg = None
        raise RuntimeError(msg or f"Workshop said HTTP {e.code}")
    except OSError as e:
        raise RuntimeError(f"cannot reach Workshop at {URL}: {e}")
    if raw:
        return out
    d = json.loads(out or b"null")
    return d["result"] if isinstance(d, dict) and "result" in d and len(d) <= 2 else d


def mr(method, path, **kw):   # Moonraker, through Workshop's guarded proxy
    return http(method, "/moonraker" + path, **kw)


def q(s):
    return urllib.parse.quote(str(s))


# ---------- tools ----------

def t_status(_):
    objs = ["print_stats", "display_status", "heater_bed", "extruder", "extruder1", "extruder2", "extruder3",
            "print_task_config", "webhooks", "filament_feed%20left", "filament_feed%20right", "pause_resume",
            "machine_state_manager", "defect_detection"]
    s = mr("GET", "/printer/objects/query?" + "&".join(objs))["status"]
    ps, ds, info = s["print_stats"], s.get("display_status", {}), s["print_stats"].get("info") or {}
    p = ds.get("progress") or 0
    lines = [f'State: {s["webhooks"]["state"] if s["webhooks"]["state"] != "ready" else ps["state"]}'
             + (f' - {ps["filename"]}' if ps.get("filename") else "")]
    if info.get("total_layer"):
        lines.append(f'Layer {info["current_layer"]} of {info["total_layer"]}, {round(p * 100)}%')
    if ps["state"] in ("printing", "paused") and ps.get("filename"):
        try:   # Workshop reads the file once and keeps it: tool changes so far, and plastic for the prime tower
            tc = http("GET", "/api/tool-changes?file=" + q(ps["filename"]), timeout=10)   # never stall the status on a first read
            pos = (mr("GET", "/printer/objects/query?virtual_sdcard=file_position")["status"].get("virtual_sdcard") or {}).get("file_position") or 0
            if tc.get("total"):
                lines.append(f'Tool change {sum(o < pos for o in tc["offsets"])} of {tc["total"]}'
                             + (f'; about {tc["tower_g"]:g} g into the prime tower (purges and tower)' if tc.get("tower_g") else ""))
        except (RuntimeError, OSError, ValueError, KeyError):
            pass
    if ps["state"] == "printing" and p > 0.02:
        left = ps["print_duration"] * (1 - p) / p
        lines.append(f"About {round(left / 60)} min left, finishes {time.strftime('%H:%M', time.localtime(time.time() + left))}")
    if ps["state"] == "paused" or (ps.get("exception") or {}).get("id"):
        import printer
        h = printer.hold(s)
        lines.append("Paused: " + (h["why"] or "no reason given") + (" Resume is possible." if h["can_resume"] else f' Not resumable yet: {h["waiting"]}.'))
    dd = s.get("defect_detection") or {}
    if dd:
        nd = dd.get("noodle") or {}
        lines.append("Spaghetti detector: " + (f'on, {nd.get("sensitivity", "?")} sensitivity' if dd.get("main_enable") and nd.get("enable") else "off"))
    lines.append(f'Bed {s["heater_bed"]["temperature"]:.0f} / {s["heater_bed"]["target"]:.0f} C')
    tc = s.get("print_task_config") or {}
    feed = {**s.get("filament_feed left", {}), **s.get("filament_feed right", {})}
    words = {"load_finish": "in the head", "preload_finish": "parked at the feeder (load it before printing with it)",
             "unload_finish": "empty"}
    for i, h in enumerate(["extruder", "extruder1", "extruder2", "extruder3"]):
        if h not in s:
            continue
        fil = " ".join(x for x in (((tc.get("filament_vendor") or [""] * 4)[i]), (tc.get("filament_type") or [""] * 4)[i],
                                   (tc.get("filament_sub_type") or [""] * 4)[i]) if x and x != "NONE") or "nothing set"
        col = ((tc.get("filament_color_rgba") or [""] * 4)[i] or "")[:6]
        ch = feed.get(f"extruder{i}", {}).get("channel_state")
        lines.append(f'T{i}: {fil}{" #" + col if col else ""}, {words.get(ch, ch or "?")}, '
                     f'{s[h]["temperature"]:.0f} / {s[h]["target"]:.0f} C{" (active)" if s[h].get("state") == "ACTIVATE" else ""}')
    try:   # the bed between two people: a finished part waiting, or cleared by whom
        b = http("GET", "/api/bed")
        if b.get("state") == "part waiting":
            lines.append(f'Bed: {b.get("file") or "a print"} is waiting on it since {time.strftime("%H:%M", time.localtime(b["t"]))}; someone must take it off first')
        elif b.get("state") == "clear" and b.get("who"):
            lines.append(f'Bed: cleared by {b["who"]} at {time.strftime("%H:%M", time.localtime(b["t"]))}')
    except (RuntimeError, KeyError, ValueError):
        pass
    try:   # which build plate is on: slicing follows it, and PETG on the smooth plate needs glue first
        lines.append(f'Plate: {http("GET", "/api/plate")["plate"]} PEI')
    except (RuntimeError, KeyError, ValueError):
        pass
    try:   # maintenance due by print hours (Workshop's own intervals)
        due = [m["what"] for m in http("GET", "/api/maintenance") if m.get("due")]
        if due:
            lines.append("Maintenance due: " + "; ".join(due) + " (mark done on the Printer tab)")
    except (RuntimeError, KeyError, ValueError, TypeError):
        pass
    try:   # grams left on each head's spool (Workshop counts what each print really used)
        heads = http("GET", "/api/spools").get("heads", {})
        left = [f'{h} {v["spool"]}: about {max(0, v["start_g"] - v["used_g"]):.0f} g left' for h, v in sorted(heads.items())]
        if left:
            lines.append("Spools: " + "; ".join(left))
    except (RuntimeError, KeyError, ValueError):
        pass
    if ps.get("message"):
        lines.append("Message: " + ps["message"])
    try:   # what the photo watch last saw (Workshop checks a layer photo every 25 layers when it has an API key)
        pw = http("GET", "/api/photo-checks")
        c = next((x for x in pw.get("checks", []) if x.get("verdict")), None)
        if c and ps.get("filename") and c.get("file") == ps["filename"]:
            lines.append(f'Photo watch: layer {c["layer"]} at {time.strftime("%H:%M", time.localtime(c["t"]))}, '
                         f'{"looks fine" if c["verdict"] == "ok" else "PAUSED: " + str(c.get("problem"))}. {c.get("reason") or ""}'.strip())
    except (RuntimeError, KeyError, ValueError):
        pass
    recent = [a for a in http("GET", "/api/alerts") if time.time() - a["t"] < 3600][:3]
    for a in recent:
        lines.append(f'Warning {time.strftime("%H:%M", time.localtime(a["t"]))}: {a["text"]}' + (f' - {a["advice"]}' if a["advice"] else ""))
    return "\n".join(lines)


def t_snapshot(_):
    jpg = http("GET", "/cam.jpg?t=" + str(time.time()), raw=True, timeout=20)
    return [{"type": "image", "data": base64.b64encode(jpg).decode(), "mimeType": "image/jpeg"},
            {"type": "text", "text": f"Camera, {time.strftime('%H:%M:%S')}. The camera sits low at bed level: it shows the plate "
                                     "and the head well, but a thin leftover part can hide."}]


def t_layer_photos(a):
    r = http("GET", "/api/frames" + (f'?job={q(a["job"])}' if a.get("job") else ""))
    fr = r["frames"]
    if not fr:
        return "No layer photos yet. Workshop saves one per layer while a print runs."
    n = max(1, min(int(a.get("count") or 4), 8))
    pick = sorted({round(i * (len(fr) - 1) / max(n - 1, 1)) for i in range(n)}) if n > 1 else [len(fr) - 1]   # spread over the job, ending at the newest
    out = []
    for i in pick:
        f = fr[i]
        out.append({"type": "image", "data": base64.b64encode(http("GET", f["url"], raw=True, timeout=20)).decode(), "mimeType": "image/jpeg"})
        out.append({"type": "text", "text": f'{r["job"]}: layer {f["layer"]} at {f["time"]}'})
    return out + [{"type": "text", "text": f'{len(fr)} photos saved for {r["job"]}; other prints with photos: {", ".join(j for j in r["jobs"] if j != r["job"]) or "none"}.'}]


def t_calibration(a):
    if a.get("value") or a.get("piece"):
        r = http("POST", "/api/calibration", {k: a[k] for k in ("test", "value", "filament", "note", "file", "piece", "save", "material") if a.get(k) not in (None, "")})
        return f'Recorded {r["test"]} = {r["value"]} for {r["filament"] or "the loaded filament"}' + (" with a photo of the print." if r.get("photo") else ".") + (
            f' The profile lives in git here: call the almehdar-workshop MCP\'s workshop_profile for {r["saved"]["material"]} with '
            + ", ".join(f"{k} = {v}" for k, v in r["saved"].items() if k not in ("pending_git", "material", "next")) + " (who = the person) to open the pull request."
            if r.get("saved")
            else f' It belongs in the profile key {r["key"]}; pass save=true to write it.' if r.get("key") else "")
    got = http("GET", "/api/calibration")
    made = got.get("made") or []
    rows = got["results"][:20]
    if made:
        rows_made = "\n".join(f'{m["file"]}: {m["test"]} on T{m["head"]} ({m["material"]}), pieces ' + ", ".join(f'{p["n"]}={p["value"]:g}' for p in m["pieces"])
                               + f'; pick {m["read"]}' for m in made[:6])
    else:
        rows_made = ""
    return "\n".join(f'{time.strftime("%d %b %H:%M", time.localtime(x["t"]))}  {x["test"]} = {x["value"]}  {x.get("filament", "")}  ({x["who"]})'
                     + (f'  key {x["key"]}' if x.get("key") else "") + (f'  - {x["note"]}' if x.get("note") else "") for x in rows) + (
        "\n\nTests made:\n" + rows_made if rows_made else "") or "No calibration results yet."


def t_calibration_test(a):
    r = http("POST", "/api/calibration-test", {k: a[k] for k in ("test", "head", "start", "step", "count", "glue") if a.get(k) not in (None, "")}, timeout=900)
    return (f'Made {r["file"]} on the printer (not started): {r["test"]} for T{r["head"]} {r["material"]}, {r["time"]}, {r["grams"]} g. Pieces: '
            + ", ".join(f'{p["n"]} = {p["value"]:g}' for p in r["pieces"]) + f'. After it prints, ask the person for {r["read"]}, then '
            f'printer_calibration with file and piece (save=true to write it into the profile). Start it like any print: bed clear and a snapshot first.')


def t_speed(a):
    pct = int(a["percent"])
    if not 50 <= pct <= 150:
        raise ValueError("speed is 50 to 150 percent")
    http("POST", "/moonraker/printer/gcode/script?script=" + q(f"M220 S{pct}"))
    return f"Speed set to {pct}%."


def t_requests(a):
    if a.get("what"):
        r = http("POST", "/api/requests", {k: a.get(k) for k in ("what", "qty", "colour", "note") if a.get(k) is not None})
        return f'Asked for {r["qty"]} x {r["what"]}' + (f' in {r["colour"]}' if r.get("colour") else "") + f' (request {r["id"]}).'
    if a.get("id") and a.get("state"):
        http("POST", "/api/requests", {"id": int(a["id"]), "state": a["state"]})
        return f'Request {a["id"]} is now {a["state"]}.'
    rows = [r for r in http("GET", "/api/requests") if r["state"] in ("open", "taken")]
    return "\n".join(f'{r["id"]}. {r["qty"]} x {r["what"]}{" in " + r["colour"] if r.get("colour") else ""} - asked by {r["who"]}, '
                     f'{"waiting" if r["state"] == "open" else "being done by " + (r.get("by") or "?")}{" - " + r["note"] if r.get("note") else ""}'
                     for r in rows) or "No open print requests."


def t_parts(a):
    p = http("GET", "/api/parts")
    if len(p["objects"]) < 2:
        return f'{p["file"] or "This print"} has {len(p["objects"])} part' + ("" if len(p["objects"]) == 1 else "s") + \
            ": nothing to cancel on its own (cancelling the only part is the whole print, which stays a person's call)."
    mark = a.get("mark")
    png = http("GET", "/parts-map.png" + (f"?mark={q(mark)}" if mark else ""), raw=True, timeout=60)
    jpg = http("GET", "/cam.jpg?t=" + str(time.time()), raw=True, timeout=20)
    rows = [f'{o["name"]}: ' + ("cancelled" if o["name"] in p["excluded"] else "printing now" if o["name"] == p["current"] else "waiting its turn")
            for o in p["objects"]]
    return [{"type": "image", "data": base64.b64encode(png).decode(), "mimeType": "image/png"},
            {"type": "image", "data": base64.b64encode(jpg).decode(), "mimeType": "image/jpeg"},
            {"type": "text", "text": f'{p["file"]}: bed from above (part outlines, ' + (f"{mark} in red, " if mark else "") +
             "first layer in blue, front edge at the bottom), then the camera now.\n" + "\n".join(rows)}]


def t_cancel_part(a):
    if a.get("person_said_yes") is not True:
        raise ValueError("ask first: show the person printer_parts with mark set to this part, and cancel only after they say yes")
    r = http("POST", "/api/parts", {"name": a["name"]})
    return f'Cancelled {r["cancelled"]}; still printing: {", ".join(r["left"])}.'


def t_files(_):
    files = sorted(mr("GET", "/server/files/list?root=gcodes"), key=lambda f: -f["modified"])[:40]
    return "\n".join(f'{time.strftime("%Y-%m-%d %H:%M", time.localtime(f["modified"]))}  {f["path"]}' for f in files) or "No files."


def t_designs(a):
    d = http("GET", "/api/designs")
    word = (a.get("search") or "").lower()
    rows = [f'bench {m["bench"]} / {m["id"]}: {m["name"]}'
            + (f' - {m["slice"]["time"]}, {m["slice"]["total_g"]} g on the {d["printer_label"]}' if (m.get("slice") or {}).get("time") else "")
            + f' - colours {", ".join(p["colour"] for p in m["parts"] if p["printed"])}'
            for m in d["designs"] if word in (m["name"] + " " + m["id"] + " " + m["for"]).lower()]
    return f'Benches: {", ".join(f"{i}: {b}" for i, b in enumerate(d["benches"]))}\n' + ("\n".join(rows) or "No designs match.")


def t_design_picture(a):
    url = f'/api/look?bench={int(a.get("bench", 0))}&id={q(a["design"])}'
    req = urllib.request.Request(URL + url, headers={"X-Workshop-User": USER})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            png, size = r.read(), r.headers.get("X-Design-Size", "")
    except urllib.error.HTTPError as e:
        raise RuntimeError(json.loads(e.read() or b"{}").get("error") or f"HTTP {e.code}")
    except OSError as e:
        raise RuntimeError(f"cannot reach Workshop at {URL}: {e}")
    return [{"type": "image", "data": base64.b64encode(png).decode(), "mimeType": "image/png"},
            {"type": "text", "text": f'{a["design"]}: top left angled, top right top, bottom left front, bottom right side. Size {size}.'}]


def t_slice(a):
    sets = []
    if a.get("infill") is not None:
        sets.append(f'infill={int(a["infill"])}')
    if a.get("supports") is not None:
        sets.append("supports=" + ("on" if a["supports"] else "off"))
    r = http("POST", "/api/slice", {"bench": a.get("bench", 0), "id": a["design"], "preset": a.get("preset") or "standard",
                                    "set": sets, "upload": True, "queue": bool(a.get("queue")),
                                    "allow_substitute": bool(a.get("allow_substitute"))}, timeout=1200)
    heads = "; ".join(f'{h["part"]} on {h["head"]} ({h["loaded"]})' for h in (r.get("heads") or []))
    return (f'{r["file"]} is on the printer{" and in the queue" if r.get("queued") else ""}: {r["time"]}, {r["total_g"]} g, '
            f'{r["changes"]} colour changes.' + (f" Heads: {heads}." if heads else "") + (f' {r["queue_note"]}' if r.get("queue_note") else ""))


def t_start(a):
    if not a.get("bed_clear"):
        raise RuntimeError("Not started: the person must say the bed is empty (bed_clear=true), and you should look at "
                           "printer_snapshot first. Ask them.")
    short = http("GET", "/api/filament-check?file=" + q(a["filename"])).get("short") or []
    if short and not a.get("filament_ok"):   # a spool that runs out mid-print spoils the part
        raise RuntimeError("Not started: " + "; ".join(short) + ". Tell the person; start anyway only if they say so (filament_ok=true).")
    http("POST", "/api/bed")   # the person said the bed is empty: Workshop logs it, and its bed check lets the start through
    mr("POST", "/printer/print/start?filename=" + q(a["filename"]))
    return f'Started {a["filename"]}. Watch the first layers with printer_snapshot.'


def t_simple(action):
    def go(_):
        mr("POST", f"/printer/print/{action}")
        return {"pause": "Paused.", "resume": "Resumed.", "cancel": "Cancelled."}[action]
    return go


def t_resume(a):
    return http("POST", "/api/control", {"action": "resume", "person_checked": a.get("person_checked") is True})["done"]


def t_detector(a):
    return http("POST", "/api/control", {"action": "detector", **{k: a[k] for k in ("spaghetti", "sensitivity") if k in a}})["done"]


WAIT_EVERY = 10   # seconds between looks while waiting


def t_wait(a):
    """Block until the print changes state (pauses, finishes, fails, starts), reaches a layer, or minutes pass, instead of
    calling printer_status over and over. Returns what happened and the status at that moment."""
    minutes = float(a.get("minutes") or 5)
    if not 0 < minutes <= 15:
        raise ValueError("minutes is up to 15")
    layer = int(a["layer"]) if a.get("layer") else None

    def look():
        ps = mr("GET", "/printer/objects/query?print_stats=state,filename,message,info")["status"]["print_stats"]
        return ps["state"], (ps.get("info") or {}).get("current_layer") or 0, ps
    state0, _, _ = look()
    end = time.time() + minutes * 60
    while True:
        state, now, ps = look()
        if state != state0:
            what = f"State changed: {state0} -> {state}" + (f' ({ps["message"]})' if ps.get("message") else "")
            break
        if layer and now >= layer:
            what = f"Reached layer {now}."
            break
        if time.time() >= end:
            what = f"Nothing changed in {minutes:g} min (still {state}" + (f", layer {now}" if now else "") + ")."
            break
        time.sleep(WAIT_EVERY)
    return what + "\n\n" + t_status({})


def t_queue(a):
    act = a.get("action", "list")
    if act == "list":
        st = mr("GET", "/server/job_queue/status")
        jobs = st["queued_jobs"]
        return f'Queue is {st["queue_state"]}.\n' + ("\n".join(f'{j["job_id"]}: {j["filename"]}' for j in jobs) or "Nothing queued.")
    if act == "add":
        mr("POST", "/server/job_queue/job?filenames=" + q(a["filename"]))
        return f'Queued {a["filename"]}.'
    if act == "remove":
        mr("DELETE", "/server/job_queue/job?job_ids=" + q(a["job_id"]))
        return "Removed."
    if act == "hold":
        mr("POST", "/server/job_queue/pause")
        return "Queue held."
    if act == "start":
        if not a.get("bed_clear"):
            raise RuntimeError("Not started: the person must say the bed is empty (bed_clear=true).")
        http("POST", "/api/bed")   # as printer_start does: the person said it is empty, so Workshop's bed check lets it through
        mr("POST", "/server/job_queue/start")
        return "Started the next job; the queue is held again behind it."
    raise RuntimeError("action is list, add, remove, hold or start")


def t_plate(a):
    if a.get("plate"):
        http("POST", "/api/plate", {"plate": a["plate"]})
    return f'The {http("GET", "/api/plate")["plate"]} plate is on. Slicing follows it; PETG on the smooth plate needs a glue-stick layer first.'


def t_unload(a):
    http("POST", "/api/unload", {"head": a["head"]})
    return f'Unloading {a["head"]}: about a minute per head. Check printer_status afterwards.'


def t_load(a):
    http("POST", "/api/load", {"head": a["head"]})
    return f'Loading {a["head"]} from its feeder into the head: a few minutes (it heats to about 270 C). Check printer_status afterwards.'


def t_filament(a):
    return http("POST", "/api/filament", {"head": a["head"], "type": a["type"], "vendor": a.get("vendor") or "generic",
                                          "sub": a.get("sub") or "generic", "colour": a["colour"].lstrip("#")})   # http() unwraps {"result": ...}


def t_filament_types(_):
    return "\n".join(f"{t}: " + "; ".join(f"{v} ({', '.join(s)})" for v, s in vs.items()) for t, vs in http("GET", "/api/filaments").items())


def t_light(a):
    mr("POST", "/printer/gcode/script?script=" + q(f'SET_LED LED=cavity_led WHITE={1 if a.get("on", True) else 0}'))
    return "Light " + ("on." if a.get("on", True) else "off.")


def t_health(_):
    h = http("GET", "/api/health")
    m = h["mesh"]
    names = {"heater_bed": "bed", "extruder": "T0", "extruder1": "T1", "extruder2": "T2", "extruder3": "T3", "temperature_sensor cavity": "chamber"}
    temps = ", ".join(f'{names.get(k, k)} {round(v["temperatures"][-1])}' + (f'/{round(v["targets"][-1])}' if v.get("targets") and v["targets"][-1] else "")
                      for k, v in h["temps"].items() if v.get("temperatures"))
    mesh = f'bed mesh spread {m["range"]} mm ({m["min"]} to {m["max"]}): {m["verdict"]}' if m.get("rows") else "no bed mesh yet"
    return f"Temperatures now (C): {temps}.\n{mesh}."


def t_spools(_):
    r = http("GET", "/api/spoolman")
    if not r["spools"]:
        return r.get("note") or "No spools in Spoolman."
    return "\n".join(f'{x["head"] or "#" + str(x["id"])}: {x["name"]}, {x["left_g"]} g left, {x["used_g"]} g used' + (" - REORDER" if x["low"] else "")
                     for x in r["spools"])


def t_costs(_):
    c = http("GET", "/api/costs")
    lines = [f'{k}: {v["prints"]} prints, {v["grams"]} g, £{v["cost"]:.2f}' for k, v in sorted(c["months"].items(), reverse=True)]
    lines += [f'{k}: {v["prints"]} prints, {v["grams"]} g, £{v["cost"]:.2f}' for k, v in sorted(c["people"].items(), key=lambda kv: -kv[1]["grams"])]
    return "\n".join(lines) or "No finished prints with filament yet."


def t_plan(_):
    p = http("GET", "/api/plan")
    if not p["jobs"]:
        return f'Nothing queued.' + (f' The printer is busy until about {p["busy_until"][11:16]}.' if p.get("busy_until") else "")
    out = [f'{j["file"]}: {j["start"][11:16]} to {j["end"][11:16]}' + (" - starts in the quiet hours, the queue holds it" if j["quiet_start"] else "") for j in p["jobs"]]
    return "\n".join(out) + f'\nQuiet hours {p["quiet"][0]:02d}:00 to {p["quiet"][1]:02d}:00: no new start; a running print may finish.'


def t_upload_model(a):
    path = os.path.expanduser(a["path"])
    if not path.lower().endswith((".stl", ".3mf")) or not os.path.isfile(path):
        raise ValueError("give the path of an .stl or .3mf file on this computer")
    params = urllib.parse.urlencode({"name": os.path.basename(path), "colour": (a.get("colour") or "888888").lstrip("#"),
                                     "preset": a.get("preset") or "lean", "queue": "1" if a.get("queue") else "0",
                                     "glue": "1" if a.get("glue") else "0", "allow_substitute": "1" if a.get("allow_substitute") else "0"})
    req = urllib.request.Request(URL + "/api/upload-model?" + params, data=open(path, "rb").read(), method="POST",
                                 headers={"X-Workshop": "1", "X-Workshop-User": USER, "Content-Type": "application/octet-stream"})
    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            d = json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError((json.loads(e.read() or b"{}") or {}).get("error") or f"Workshop said HTTP {e.code}")
    return (f'{d["file"]} is on the printer: {d["time"]}, {d["total_g"]} g, preset {d["preset"]}' + (", queued" if d.get("queued") else "")
            + (f'. {d["queue_note"]}' if d.get("queue_note") else "") + ". Nothing starts until a person says the bed is clear.")


HEATERS = {"T0": "extruder", "T1": "extruder1", "T2": "extruder2", "T3": "extruder3", "bed": "heater_bed"}
MAX_C = {"bed": 110}   # nozzles 300 (the U1's hot ends); the bed 110


def t_doctor(_):
    r = http("GET", "/api/doctor")["reports"]
    if not r:
        return "No print doctor reports yet: it runs by itself after a pause, a failure or a flawed finish."
    out = []
    for x in r[:5]:
        when = time.strftime("%d %b %H:%M", time.localtime(x["t"]))
        out.append(f'{when} {x["file"]}: {x.get("diagnosis") or x.get("error")}' + "".join(f'\n  - {c["setting"]}: {c["now"]} -> {c["suggested"]} ({c["why"]})' for c in x.get("changes", []))
                   + (f'\n  Next: {x["next_step"]}' if x.get("next_step") else ""))
    return "\n\n".join(out)


def t_fan(a):
    return http("POST", "/api/control", {"action": "fan", "fan": a.get("fan", "part"), "percent": a["percent"]})["done"]


def t_calibrate(a):
    if a.get("nothing_in_the_way") is not True:
        raise ValueError("calibration moves the head and bed: check the camera (printer_view_bed), ask the person, then pass nothing_in_the_way=true")
    out = http("POST", "/api/control", {"action": "calibrate", "which": a["which"], "nothing_in_the_way": True}, timeout=960)["done"]
    return out + "\n" + t_console({"lines": 15})


def t_console(a):
    rows = mr("GET", "/server/gcode_store?count=120")["gcode_store"]
    rows = [g for g in rows if not g["message"].startswith(("B:", "T0", "ok"))][-int(a.get("lines", 30)):]
    return "\n".join(("> " if g["type"] == "command" else "") + g["message"] for g in rows) or "Nothing in the console."


def t_heat(a):
    """Set one heater's target (0 turns it off). Workshop refuses it while a print runs."""
    h, c = str(a.get("heater")), float(a.get("celsius", -1))
    if h not in HEATERS:
        raise ValueError("heater is T0, T1, T2, T3 or bed")
    if not 0 <= c <= MAX_C.get(h, 300):
        raise ValueError(f"{h} takes 0 to {MAX_C.get(h, 300)} C")
    mr("POST", "/printer/gcode/script?script=" + q(f"SET_HEATER_TEMPERATURE HEATER={HEATERS[h]} TARGET={c:g}"))
    return f"{h} set to {c:g} C." if c else f"{h} off."


LIMITS = {"x": (0, 271), "y": (0, 335)}
GAP = 20   # mm the nozzle stays above the tallest thing on the plate


def last_height():
    try:
        return mr("GET", "/server/history/list?limit=1&order=desc")["jobs"][0].get("metadata", {}).get("object_height")
    except (RuntimeError, KeyError, IndexError):
        return None


def t_move(a):
    """Move the head (X, Y) and the bed (Z) to absolute positions, homed only; Z never closer than GAP mm over the
    tallest thing on the plate (the last print's height unless the person says tallest_mm)."""
    th = mr("GET", "/printer/objects/query?toolhead=homed_axes")["status"]["toolhead"]
    if th.get("homed_axes") != "xyz":
        raise ValueError("not homed: use printer_view_bed (it homes safely) or printer_control home first")
    parts = []
    for ax in ("x", "y"):
        if a.get(ax) is not None:
            v = float(a[ax]); lo, hi = LIMITS[ax]
            if not lo <= v <= hi:
                raise ValueError(f"{ax.upper()} is {lo} to {hi} mm")
            parts.append(f"{ax.upper()}{v:g}")
    if a.get("z") is not None:
        z, tall = float(a["z"]), a.get("tallest_mm")
        tall = float(tall) if tall is not None else last_height()
        if tall is None:
            raise ValueError("say tallest_mm (how tall the tallest thing on the plate is) before moving the bed up")
        low = max(5.0, tall + GAP)
        if not low <= z <= 275:
            raise ValueError(f"Z is {low:g} to 275 mm here: the nozzle stays {GAP} mm over {tall:g} mm on the plate")
        parts.append(f"Z{z:g}")
    if not parts:
        raise ValueError("give x, y and/or z")
    mr("POST", "/printer/gcode/script?script=" + q("G90"))
    mr("POST", "/printer/gcode/script?script=" + q("G1 " + " ".join(parts) + f" F{int(a.get('speed_mm_s', 50)) * 60}"))
    return "Moved to " + " ".join(parts) + "."


RAW_NEVER = re.compile(r"^(SAVE_CONFIG|FIRMWARE_RESTART|RESTART|FORCE_MOVE|SET_KINEMATIC_POSITION|SET_STEPPER_ENABLE|STEPPER_BUZZ|"
                       r"Z_ENDSTOP_CALIBRATE|Z_OFFSET_APPLY|M502|M500)\b", re.I)
RAW_MOVES = re.compile(r"^(G0|G1|G2|G3|G28|G29|M84|SENSORLESS_HOME|PROBE|BED_MESH_CALIBRATE|AUTO_BED_MESH|SHAPER_CALIBRATE|"
                       r"TEST_RESONANCES|XYZ_OFFSET_CALIBRATE|PROBE_XYZ|AUTO_SCREWS|SCREWS_TILT|MOVE_TO_|AUTO_FEEDING|M600|M701|M702)", re.I)


def t_gcode(a):
    """Any one G-code command or macro, for what the other tools do not cover. Workshop refuses it while a print runs.
    Commands that move or feed need nothing_in_the_way; ones that rewrite the printer's setup are never sent."""
    cmd, why = str(a.get("command", "")).strip(), str(a.get("why", "")).strip()
    if not cmd or "\n" in cmd or len(cmd) > 200:
        raise ValueError("one command, under 200 characters")
    if not why:
        raise ValueError("say why (it goes in the activity log)")
    if RAW_NEVER.match(cmd):
        raise ValueError("that rewrites the printer's own setup or forces a motor: a person does it from the printer")
    if RAW_MOVES.match(cmd) and a.get("nothing_in_the_way") is not True:
        raise ValueError("that moves the head or bed, or feeds filament: check the camera (printer_view_bed), ask the person, then pass nothing_in_the_way=true")
    http("POST", "/moonraker/printer/gcode/script?script=" + q(cmd), timeout=600)
    try:   # what the printer said back
        out = [g["message"] for g in mr("GET", "/server/gcode_store?count=12")["gcode_store"] if g["type"] == "response"][-6:]
    except (RuntimeError, KeyError):
        out = []
    return f"Sent {cmd} ({why}).\n" + "\n".join(out)


CONTROLS = {"heaters_off": ("TURN_OFF_HEATERS", "Heaters off."), "motors_off": ("M84", "Motors off."), "home": ("G28", "Homed.")}


def t_control(a):
    act = a.get("action")
    if act not in CONTROLS:
        raise ValueError("action is heaters_off, motors_off or home")
    if act == "home" and a.get("nothing_in_the_way") is not True:
        raise ValueError("home moves the head: ask the person whether anything is in its way, then pass nothing_in_the_way=true")
    cmd, done = CONTROLS[act]
    mr("POST", "/printer/gcode/script?script=" + q(cmd))   # Workshop refuses these while a job runs
    return done


def t_view_bed(a):
    """Bed up to the camera so the plate can be seen; then a picture."""
    body = {} if a.get("tallest_mm") is None else {"tallest_mm": a["tallest_mm"]}
    v = http("POST", "/api/view-bed", body, timeout=300)
    out = t_snapshot({})
    out.append({"type": "text", "text": f"The bed is at Z {v['z']} mm" + (" (homed first: the motors were off)" if v.get("homed") else "") +
                ". Z homes downwards on the U1, so homing never pushes the bed into the head."})
    return out


def t_pause_at_layer(a):
    if a.get("off"):
        mr("POST", "/printer/gcode/script?script=" + q("SET_PAUSE_AT_LAYER ENABLE=0"))
        return "No pause at a layer any more."
    n = int(a["layer"])
    if not 1 <= n <= 9999:
        raise ValueError("layer is a number from 1")
    mr("POST", "/printer/gcode/script?script=" + q(f"SET_PAUSE_AT_LAYER LAYER={n}"))
    return f"The printer pauses itself at layer {n} (for magnets, nuts or a colour change by hand); resume with printer_resume."


def t_screen(_):
    png = http("GET", "/screen.jpg?t=" + str(time.time()), raw=True, timeout=20)
    kind = "image/png" if png[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
    return [{"type": "image", "data": base64.b64encode(png).decode(), "mimeType": kind},
            {"type": "text", "text": "The printer's own touchscreen right now (view only)."}]


def t_history(a):
    jobs = mr("GET", f'/server/history/list?limit={int(a.get("count") or 10)}&order=desc')["jobs"]
    return "\n".join(f'{time.strftime("%Y-%m-%d %H:%M", time.localtime(j["start_time"]))}  {j["status"]:<10} '
                     f'{j["print_duration"] / 60:.0f} min  {j["filament_used"] / 1000:.2f} m  {j["filename"]}' for j in jobs) or "No prints yet."


def t_activity(_):
    rows = http("GET", "/api/activity")[:20]
    return "\n".join(f'{time.strftime("%H:%M", time.localtime(r["t"]))}  {r["who"]} {r["what"]}'
                     + (f': {r["detail"]}' if r["detail"] else "") + f' ({r["via"]})' for r in rows) or "Nothing yet."


def t_alerts(_):
    rows = http("GET", "/api/alerts")
    return "\n".join(f'{time.strftime("%m-%d %H:%M", time.localtime(a["t"]))}  {a["text"]}' + (f' - {a["advice"]}' if a["advice"] else "")
                     for a in rows) or "No warnings."


HEAD = {"type": "string", "description": "T0, T1, T2 or T3 (the printer's screen numbers them 1 to 4)"}
TOOLS = [
    ("printer_status", "What the printer is doing: state, layer, time left, bed, each head's filament and whether it is loaded, recent warnings.", {}, t_status),
    ("printer_wait", "Wait for the print instead of polling printer_status: returns when the state changes (paused, finished, cancelled, "
     "error, started), when it reaches a layer, or after the minutes given (default 5, at most 15), with the status then.",
     {"layer": {"type": "integer", "minimum": 1, "description": "return once the print reaches this layer"},
      "minutes": {"type": "number", "description": "longest wait, up to 15"}}, t_wait),
    ("printer_snapshot", "A fresh picture from the camera inside the printer. Look at it before every start (the plate must be on and empty) and to check a print.", {}, t_snapshot),
    ("printer_layer_photos", "Photos Workshop saved during a print, one per layer: spread across the job and ending at the newest. Use to see how a long or overnight print went without a live look.",
     {"count": {"type": "integer", "description": "how many photos, 1 to 8 (default 4)"}, "job": {"type": "string", "description": "a past print's name; default the latest"}}, t_layer_photos),
    ("printer_calibration", "Calibration results (print-calibration skill): with no value, list them; with test and value, record the value the person picked from a test print, with a camera photo of it.",
     {"test": {"type": "string", "enum": ["temperature", "max-flow", "pressure-advance", "flow-ratio", "retraction", "other"]},
      "value": {"type": "string", "description": "the value picked, e.g. 240"}, "filament": {"type": "string"}, "note": {"type": "string"},
      "file": {"type": "string", "description": "a test made by printer_calibration_test: give piece instead of value"},
      "piece": {"type": "integer", "description": "the numbered piece the person picked"},
      "save": {"type": "boolean", "description": "also mark it as a profile change: the reply says the workshop_profile call that opens the pull request; only when the person agrees"},
      "material": {"type": "string", "description": "with save and a value (no file): the profile name, e.g. petg"}}, t_calibration),
    ("printer_calibration_test", "Make a one-tap calibration test for one head and put it on the printer (never starts it): pressure-advance "
     "(8 boxes, one per PA value), flow-ratio (9 tiles round the profile's flow) or temperature (5 bands, 5 C apart from 10 above the "
     "profile). Each piece is numbered; after printing the person says which piece is best and printer_calibration records it.",
     {"test": {"type": "string", "enum": ["pressure-advance", "flow-ratio", "temperature"]}, "head": {"type": "integer", "minimum": 0, "maximum": 3},
      "start": {"type": "number"}, "step": {"type": "number"}, "count": {"type": "integer", "minimum": 2, "maximum": 12},
      "glue": {"type": "boolean", "description": "PETG on the smooth plate only: the person confirmed glue stick is on"}}, t_calibration_test, ["test", "head"]),
    ("printer_requests", "Print requests between the two of us: with no arguments, the open ones; with what (and qty, colour, note), ask for a print; with id and state (taken, done, dropped), move one along.",
     {"what": {"type": "string"}, "qty": {"type": "integer"}, "colour": {"type": "string"}, "note": {"type": "string"},
      "id": {"type": "integer"}, "state": {"type": "string", "enum": ["open", "taken", "done", "dropped"]}}, t_requests),
    ("printer_files", "Sliced G-code files on the printer, newest first.", {}, t_files),
    ("printer_designs", "Designs Claude can slice (from the Print Benches), with real print time and grams where known.",
     {"search": {"type": "string", "description": "words to filter by"}}, t_designs),
    ("printer_design_picture", "A picture of a design from four sides (angled, top, front, side) in its print colours, with its size in mm. Look at it before slicing to check it is the right thing.",
     {"design": {"type": "string", "description": "design id from printer_designs"}, "bench": {"type": "integer"}}, t_design_picture, ["design"]),
    ("printer_slice", "Slice a design for the printer with the filament that is loaded (each colour goes to the head holding it) and put the G-code on the printer. Refuses a colour that is not loaded unless allow_substitute.",
     {"design": {"type": "string", "description": "design id from printer_designs"}, "bench": {"type": "integer"},
      "preset": {"type": "string", "enum": ["draft", "standard", "strong", "fine", "low-waste"]},
      "infill": {"type": "integer", "description": "percent"}, "supports": {"type": "boolean"},
      "queue": {"type": "boolean", "description": "also add it to the job queue"}, "allow_substitute": {"type": "boolean"}}, t_slice, ["design"]),
    ("printer_start", "Start printing a file now. Only when the person has said the bed is empty (bed_clear) and printer_snapshot shows an empty plate. The printer also refuses if a head the file uses is not loaded or the file would leave the bed cold.",
     {"filename": {"type": "string"}, "bed_clear": {"type": "boolean", "description": "true only if the person said the bed is empty"},
      "filament_ok": {"type": "boolean", "description": "true only if the person said to start although a spool looks short"}}, t_start, ["filename", "bed_clear"]),
    ("printer_pause", "Pause the current print.", {}, t_simple("pause")),
    ("printer_resume", "Resume a paused print, including one the printer's spaghetti detector paused. Refused, with the reason, "
     "until the printer will really take it. After the printer's own detector paused it, Workshop refuses unless person_checked: "
     "look at printer_snapshot, ask the person, and pass person_checked=true only when they say the part is fine.",
     {"person_checked": {"type": "boolean", "description": "the person looked at the part after a detector pause and said carry on"}}, t_resume),
    ("printer_detector", "The printer's own spaghetti detector (its camera pauses the print when it sees loose strands). Turn it on or "
     "off or set its sensitivity; the printer keeps the setting until it is changed back, so say so and offer to turn it back on.",
     {"spaghetti": {"type": "boolean", "description": "true on, false off"},
      "sensitivity": {"type": "string", "enum": ["low", "high"]}}, t_detector),
    ("printer_cancel", "Cancel the current print (it cannot be resumed). Confirm with the person first unless the print is failing.", {}, t_simple("cancel")),
    ("printer_queue", "The job queue: list, add a file, remove a job, hold the queue, or start the next job (bed_clear needed).",
     {"action": {"type": "string", "enum": ["list", "add", "remove", "hold", "start"]}, "filename": {"type": "string"},
      "job_id": {"type": "string"}, "bed_clear": {"type": "boolean"}}, t_queue, ["action"]),
    ("printer_unload", "Unload a head's filament back to its spool (Snapmaker U1), or all heads. Not during a print.",
     {"head": {"type": "string", "description": "T0 to T3, or all"}}, t_unload, ["head"]),
    ("printer_load", "Load a head whose filament is parked at its feeder into the head (the printer's own feed routine; it heats and feeds). Not while printing. Use it when a start is refused because a head is not loaded.",
     {"head": {"type": "string", "enum": ["T0", "T1", "T2", "T3", "all"]}}, t_load, ["head"]),
    ("printer_set_filament", "Tell the printer what spool was loaded on a head, as its screen asks after loading (type from printer_filament_types; an unknown brand uses the type's generic settings). Not during a print.",
     {"head": HEAD, "type": {"type": "string", "description": "e.g. PLA, PETG"}, "vendor": {"type": "string", "description": "brand, e.g. SUNLU"},
      "sub": {"type": "string", "description": "sub-type, e.g. generic, HF, Silk"}, "colour": {"type": "string", "description": "hex RRGGBB"}},
     t_filament, ["head", "type", "colour"]),
    ("printer_filament_types", "The filament types, brands and sub-types the printer knows.", {}, t_filament_types),
    ("printer_parts", "The parts of the running print (one per object the slicer defined): which is printing, waiting or cancelled, with a top-down bed picture and the camera. With mark, that part is filled red: show this to the person before printer_cancel_part.",
     {"mark": {"type": "string", "description": "a part name to fill red, as printer_parts lists it"}}, t_parts),
    ("printer_cancel_part", "Stop printing ONE part and keep the rest going (EXCLUDE_OBJECT). Ask first: show printer_parts with mark set to it and wait for the person's yes. Workshop refuses the last part left (that is the whole print, a person's own cancel).",
     {"name": {"type": "string", "description": "the part's name from printer_parts"}, "person_said_yes": {"type": "boolean", "description": "true only after the person saw the marked picture and said yes"}}, t_cancel_part,
     ["name", "person_said_yes"]),
    ("printer_health", "Printer health: every heater's temperature and target now, and the last bed mesh (spread and whether the bed is flat enough).", {}, t_health),
    ("printer_spools", "The spools in Spoolman, head by head: grams left, used, and which need reordering (under 200 g).", {}, t_spools),
    ("printer_costs", "Filament and cost per month and per person who started the prints, from the printer's own measure.", {}, t_costs),
    ("printer_plan", "The queue as a plan: when each job would start and finish after the current print, and any that would start in the quiet hours (03:00 to 08:00, when no new print starts).", {}, t_plan),
    ("printer_plate", "Which build plate is on the printer (textured or smooth PEI), or record that the person swapped it. Slicing uses it.",
     {"plate": {"type": "string", "enum": ["textured", "smooth"], "description": "set only when the person says they changed the plate"}}, t_plate, []),
    ("printer_upload_model", "Slice an STL or 3MF from this computer on the Workshop computer and send it to the printer (optionally queue it). An STL goes on the head holding the given colour. A painted or multicolour 3MF saved for another printer (Bambu H2D, X2D, A1) keeps its colours, each on the loaded head nearest in colour, and its supports and prime tower; if a colour it uses is not loaded it is refused unless allow_substitute. Never starts a print.",
     {"path": {"type": "string", "description": "the .stl or .3mf file on this computer"}, "colour": {"type": "string", "description": "hex colour of the loaded filament to use, e.g. 1c1c1c (STL only)"},
      "preset": {"type": "string", "description": "lean (default), quality, strong, fine, polished, draft or standard"}, "queue": {"type": "boolean"},
      "glue": {"type": "boolean", "description": "PETG on the smooth plate only: the person confirmed a glue-stick layer is on"},
      "allow_substitute": {"type": "boolean", "description": "slice anyway with the nearest loaded colour (ask the person first)"}}, t_upload_model, ["path"]),
    ("printer_control", "Heaters off, motors off, or home the printer. Workshop refuses all three while a print runs. Home moves the head: ask the person first and pass nothing_in_the_way.",
     {"action": {"type": "string", "enum": ["heaters_off", "motors_off", "home"]}, "nothing_in_the_way": {"type": "boolean"}}, t_control, ["action"]),
    ("printer_doctor", "The print doctor's latest reports: after a pause, a failure or a flawed finish, Claude reads the layer photos and slicer settings and suggests setting changes. Read them before re-slicing a print that went wrong.", {}, t_doctor),
    ("printer_fan", "Set a fan: part (the cooling fan on the head; works mid-print), cavity, exhaust or circulation (idle only), 0 to 100 percent.",
     {"fan": {"type": "string", "enum": ["part", "cavity", "exhaust", "circulation"]}, "percent": {"type": "number", "minimum": 0, "maximum": 100}}, t_fan, ["percent"]),
    ("printer_calibrate", "Run a calibration: shaper (input shaping, the head shakes for minutes), offsets (lines the four heads up), mesh (probes the whole bed) or screws (says how far to turn each bed screw). Idle only, homes first; the plate must be clear: check printer_view_bed and ask the person, then pass nothing_in_the_way. Returns the console's result lines.",
     {"which": {"type": "string", "enum": ["shaper", "offsets", "mesh", "screws"]}, "nothing_in_the_way": {"type": "boolean"}}, t_calibrate, ["which"]),
    ("printer_console", "The printer's own console: the last commands and its replies (results of calibrations, errors, M114 positions).",
     {"lines": {"type": "integer", "minimum": 5, "maximum": 100}}, t_console),
    ("printer_heat", "Set a heater's target temperature (T0 to T3 up to 300 C, the bed up to 110 C; 0 turns it off). Not while a print runs.",
     {"heater": {"type": "string", "enum": list(HEATERS)}, "celsius": {"type": "number", "minimum": 0, "maximum": 300}}, t_heat, ["heater", "celsius"]),
    ("printer_move", "Move the head (x 0-271, y 0-335) and the bed (z: 0 is at the nozzle, 275 the bottom) to absolute positions. Homed only, not while printing. The bed never comes closer than 20 mm over the tallest thing on the plate (last print's height, or tallest_mm). Check the camera first.",
     {"x": {"type": "number"}, "y": {"type": "number"}, "z": {"type": "number"}, "tallest_mm": {"type": "number"}, "speed_mm_s": {"type": "integer", "minimum": 5, "maximum": 200}}, t_move),
    ("printer_gcode", "Send one G-code command or macro the other tools do not cover (calibration macros such as SHAPER_CALIBRATE or XYZ_OFFSET_CALIBRATE_ALL, SET_SPOOL_ID, queries such as M114). Not while printing. Anything that moves or feeds needs nothing_in_the_way=true after a camera check and the person's word; SAVE_CONFIG, restarts and forced motor moves are never sent. Returns the printer's reply.",
     {"command": {"type": "string"}, "why": {"type": "string"}, "nothing_in_the_way": {"type": "boolean"}}, t_gcode, ["command", "why"]),
    ("printer_pause_at_layer", "Make the running print pause itself at a layer, for magnets, nuts or inserts dropped into pockets; or off=true to cancel that.",
     {"layer": {"type": "integer"}, "off": {"type": "boolean"}}, t_pause_at_layer),
    ("printer_view_bed", "Bring the bed up to the camera so you can see the plate (after a print the bed drops out of view). Idle only. Homes first if the motors are off (Z homes downwards, away from the head), then raises the bed until the head is 20 mm above the tallest thing on the plate, which is the last print's height, or tallest_mm when the person says something taller is there. Returns a picture.",
     {"tallest_mm": {"type": "number", "minimum": 0, "maximum": 230}}, t_view_bed),
    ("printer_screen", "A picture of what the printer's own touchscreen shows right now.", {}, t_screen),
    ("printer_speed", "Set the print speed override (as on the printer screen), 50 to 150 percent; safe mid-print. Say why and watch the next layers after a big change.",
     {"percent": {"type": "integer", "minimum": 50, "maximum": 150}}, t_speed, ["percent"]),
    ("printer_light", "Turn the chamber light on or off.", {"on": {"type": "boolean"}}, t_light),
    ("printer_history", "Past prints with their real time and filament used.", {"count": {"type": "integer"}}, t_history),
    ("printer_activity", "Who did what to the printer recently (from Workshop, Claude and the command line).", {}, t_activity),
    ("printer_warnings", "The printer's warnings and errors, kept even after its own console has scrolled past them, with advice.", {}, t_alerts),
]
BY_NAME = {t[0]: t for t in TOOLS}


def tool_list():
    out = []
    for t in TOOLS:
        name, desc, props, _ = t[:4]
        schema = {"type": "object", "properties": props}
        if len(t) > 4:
            schema["required"] = t[4]
        out.append({"name": name, "description": desc, "inputSchema": schema})
    return out


INSTRUCTIONS = ("These tools drive a 3D printer (a Snapmaker U1 with four heads, T0 to T3) through the household's Workshop page. "
                "Safety: start a print only when the person has said the bed is empty AND printer_snapshot shows an empty plate; "
                "never start on the picture alone. After starting, check the first layers with printer_snapshot. "
                "Before printing with a head, printer_status must say it is in the head (parked filament runs out at once). "
                "Every action is logged with the person's name.")


def handle(msg):
    method, mid = msg.get("method"), msg.get("id")
    if method == "initialize":
        return {"protocolVersion": (msg.get("params") or {}).get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {}}, "serverInfo": {"name": "printer", "version": VERSION}, "instructions": INSTRUCTIONS}
    if method == "tools/list":
        return {"tools": tool_list()}
    if method == "tools/call":
        p = msg.get("params") or {}
        t = BY_NAME.get(p.get("name"))
        if not t:
            return {"content": [{"type": "text", "text": f"no tool {p.get('name')}"}], "isError": True}
        try:
            out = t[3](p.get("arguments") or {})
            return {"content": out if isinstance(out, list) else [{"type": "text", "text": out}]}
        except Exception as e:   # one bad call never takes the tools out of the Claude app
            return {"content": [{"type": "text", "text": f"{e}"}], "isError": True}
    if method == "ping":
        return {}
    if mid is not None:
        raise LookupError(method)
    return None   # a notification


def main():
    for stream in (sys.stdin, sys.stdout):   # MCP speaks UTF-8; Windows defaults to its own code page (seen on the Legion)
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        try:
            res = handle(msg)
            reply = None if msg.get("id") is None else {"jsonrpc": "2.0", "id": msg["id"], "result": res}
        except LookupError as e:
            reply = {"jsonrpc": "2.0", "id": msg.get("id"), "error": {"code": -32601, "message": f"unknown method {e}"}}
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
