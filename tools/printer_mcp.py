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
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

URL = os.environ.get("WORKSHOP_URL", "http://127.0.0.1:8090").rstrip("/")
USER = os.environ.get("WORKSHOP_USER", "someone")
VERSION = "1.0.0"


def http(method, path, body=None, raw=False, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"X-Workshop": "1", "X-Workshop-User": USER}
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
            "print_task_config", "webhooks", "filament_feed%20left", "filament_feed%20right"]
    s = mr("GET", "/printer/objects/query?" + "&".join(objs))["status"]
    ps, ds, info = s["print_stats"], s.get("display_status", {}), s["print_stats"].get("info") or {}
    p = ds.get("progress") or 0
    lines = [f'State: {s["webhooks"]["state"] if s["webhooks"]["state"] != "ready" else ps["state"]}'
             + (f' - {ps["filename"]}' if ps.get("filename") else "")]
    if info.get("total_layer"):
        lines.append(f'Layer {info["current_layer"]} of {info["total_layer"]}, {round(p * 100)}%')
    if ps["state"] == "printing" and p > 0.02:
        left = ps["print_duration"] * (1 - p) / p
        lines.append(f"About {round(left / 60)} min left, finishes {time.strftime('%H:%M', time.localtime(time.time() + left))}")
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
    if ps.get("message"):
        lines.append("Message: " + ps["message"])
    recent = [a for a in http("GET", "/api/alerts") if time.time() - a["t"] < 3600][:3]
    for a in recent:
        lines.append(f'Warning {time.strftime("%H:%M", time.localtime(a["t"]))}: {a["text"]}' + (f' - {a["advice"]}' if a["advice"] else ""))
    return "\n".join(lines)


def t_snapshot(_):
    jpg = http("GET", "/cam.jpg?t=" + str(time.time()), raw=True, timeout=20)
    return [{"type": "image", "data": base64.b64encode(jpg).decode(), "mimeType": "image/jpeg"},
            {"type": "text", "text": f"Camera, {time.strftime('%H:%M:%S')}. The camera sits low at bed level: it shows the plate "
                                     "and the head well, but a thin leftover part can hide."}]


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
    mr("POST", "/printer/print/start?filename=" + q(a["filename"]))
    return f'Started {a["filename"]}. Watch the first layers with printer_snapshot.'


def t_simple(action):
    def go(_):
        mr("POST", f"/printer/print/{action}")
        return {"pause": "Paused.", "resume": "Resumed.", "cancel": "Cancelled."}[action]
    return go


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
        mr("POST", "/server/job_queue/start")
        return "Started the next job; the queue is held again behind it."
    raise RuntimeError("action is list, add, remove, hold or start")


def t_unload(a):
    http("POST", "/api/unload", {"head": a["head"]})
    return f'Unloading {a["head"]}: about a minute per head. Check printer_status afterwards.'


def t_filament(a):
    return http("POST", "/api/filament", {"head": a["head"], "type": a["type"], "vendor": a.get("vendor") or "generic",
                                          "sub": a.get("sub") or "generic", "colour": a["colour"].lstrip("#")})   # http() unwraps {"result": ...}


def t_filament_types(_):
    return "\n".join(f"{t}: " + "; ".join(f"{v} ({', '.join(s)})" for v, s in vs.items()) for t, vs in http("GET", "/api/filaments").items())


def t_light(a):
    mr("POST", "/printer/gcode/script?script=" + q(f'SET_LED LED=cavity_led WHITE={1 if a.get("on", True) else 0}'))
    return "Light " + ("on." if a.get("on", True) else "off.")


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
    ("printer_snapshot", "A fresh picture from the camera inside the printer. Look at it before every start (the plate must be on and empty) and to check a print.", {}, t_snapshot),
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
     {"filename": {"type": "string"}, "bed_clear": {"type": "boolean", "description": "true only if the person said the bed is empty"}}, t_start, ["filename", "bed_clear"]),
    ("printer_pause", "Pause the current print.", {}, t_simple("pause")),
    ("printer_resume", "Resume a paused print.", {}, t_simple("resume")),
    ("printer_cancel", "Cancel the current print (it cannot be resumed). Confirm with the person first unless the print is failing.", {}, t_simple("cancel")),
    ("printer_queue", "The job queue: list, add a file, remove a job, hold the queue, or start the next job (bed_clear needed).",
     {"action": {"type": "string", "enum": ["list", "add", "remove", "hold", "start"]}, "filename": {"type": "string"},
      "job_id": {"type": "string"}, "bed_clear": {"type": "boolean"}}, t_queue, ["action"]),
    ("printer_unload", "Unload a head's filament back to its spool (Snapmaker U1), or all heads. Not during a print.",
     {"head": {"type": "string", "description": "T0 to T3, or all"}}, t_unload, ["head"]),
    ("printer_set_filament", "Tell the printer what spool was loaded on a head, as its screen asks after loading (type from printer_filament_types; an unknown brand uses the type's generic settings). Not during a print.",
     {"head": HEAD, "type": {"type": "string", "description": "e.g. PLA, PETG"}, "vendor": {"type": "string", "description": "brand, e.g. SUNLU"},
      "sub": {"type": "string", "description": "sub-type, e.g. generic, HF, Silk"}, "colour": {"type": "string", "description": "hex RRGGBB"}},
     t_filament, ["head", "type", "colour"]),
    ("printer_filament_types", "The filament types, brands and sub-types the printer knows.", {}, t_filament_types),
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
        except (RuntimeError, KeyError, ValueError, TypeError) as e:
            return {"content": [{"type": "text", "text": f"{e}"}], "isError": True}
    if method == "ping":
        return {}
    if mid is not None:
        raise LookupError(method)
    return None   # a notification


def main():
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
