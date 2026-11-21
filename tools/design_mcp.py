"""Design-and-slice tools for the Claude desktop app (and Claude Code): an MCP server over this repo's own scripts.

With it, Claude in the desktop app can write an OpenSCAD design, look at it, export its colour parts, slice it in
OrcaSlicer with named presets and settings, read your printer's measured fits and build a colour 3MF, the same way
Claude Code does from a terminal. Driving a printer is the other server, printer_mcp.py.

    DESIGN_DIR=~/3d-designs python design_mcp.py

Claude desktop app, Settings > Developer > Edit Config (claude_desktop_config.json):
    {"mcpServers": {"design": {"command": "python3", "args": ["/path/to/claude-3d-printing/tools/design_mcp.py"],
                               "env": {"DESIGN_DIR": "/Users/you/3d-designs"}}}}
Claude Code:  claude mcp add design -e DESIGN_DIR=~/3d-designs -- python3 /path/to/tools/design_mcp.py

Files are written only inside DESIGN_DIR (default ~/3d-designs); models may be read from anywhere. OrcaSlicer and
OpenSCAD are found the same way the scripts find them (ORCA_CLI, OPENSCAD). SPOOLMAN_URL, when set, adds spool stock
to slicing and a spools tool. Speaks MCP over stdio (JSON-RPC, one message per line). Standard library only.
"""
import base64
import json
import os
import subprocess
import sys
import tempfile
import urllib.request

TOOLS = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")
DESIGN_DIR = os.path.abspath(os.path.expanduser(os.environ.get("DESIGN_DIR") or os.path.join(HOME, "3d-designs")))
SPOOLMAN = os.environ.get("SPOOLMAN_URL", "").rstrip("/")
VERSION = "1.0.0"
TEXT_TYPES = (".scad", ".json", ".txt", ".md")


def run(script, *args, timeout=900):
    """One of this folder's scripts; its output is the answer, its error (it exits non-zero with a message) the error."""
    r = subprocess.run([sys.executable, os.path.join(TOOLS, script), *map(str, args)], capture_output=True, text=True,
                       timeout=timeout, cwd=DESIGN_DIR if os.path.isdir(DESIGN_DIR) else None)
    if r.returncode:
        raise RuntimeError((r.stdout + "\n" + r.stderr).strip() or f"{script} failed")
    return r.stdout.strip()


def path_in(name, must_exist=False):
    """A path for a file to write or read: relative names land in DESIGN_DIR; anything written must stay inside it."""
    p = os.path.abspath(os.path.join(DESIGN_DIR, os.path.expanduser(name)))
    if must_exist and not os.path.exists(p):
        raise RuntimeError(f"no such file: {p}")
    return p


def inside(p):
    """Real paths, so a link inside the folder that points out of it does not count as inside; another drive is outside."""
    root = os.path.realpath(DESIGN_DIR)
    try:
        return os.path.commonpath([os.path.realpath(p), root]) == root
    except ValueError:
        return False


def t_save(a):
    p = path_in(a["path"])
    if not inside(p):
        raise RuntimeError(f"files are written only inside {DESIGN_DIR}")
    if not p.lower().endswith(TEXT_TYPES):
        raise RuntimeError("only .scad, .json, .txt and .md files are written here")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(a["content"])
    return f"saved {p} ({len(a['content'])} characters)"


def t_read(a):
    p = path_in(a["path"], must_exist=True)
    if not p.lower().endswith(TEXT_TYPES):
        raise RuntimeError("only text files (.scad, .json, .txt, .md) are read here")
    return open(p, encoding="utf-8", errors="replace").read()


def t_files(_):
    if not os.path.isdir(DESIGN_DIR):
        return f"{DESIGN_DIR} is empty (it is made on the first save)"
    rows = []
    for root, _dirs, files in os.walk(DESIGN_DIR):
        for f in sorted(files):
            p = os.path.join(root, f)
            rows.append(f"{os.path.relpath(p, DESIGN_DIR)}  {os.path.getsize(p)} bytes")
    return "\n".join(rows) or f"{DESIGN_DIR} is empty"


def t_look(a):
    scad = path_in(a["path"], must_exist=True)
    out = os.path.join(tempfile.mkdtemp(prefix="look-"), "look.png")
    args = [scad, "--out", out, "--size", a.get("size", 1000)]
    if a.get("part"):
        args += ["--part", a["part"]]
    for k, v in (a.get("set") or {}).items():
        args += ["-D", f"{k}={v}"]
    text = run("look.py", *args, timeout=600)
    img = base64.b64encode(open(out, "rb").read()).decode()
    return [{"type": "text", "text": text}, {"type": "image", "data": img, "mimeType": "image/png"}]


def t_export(a):
    scad = path_in(a["path"], must_exist=True)
    out = path_in(a.get("out") or os.path.join(os.path.dirname(scad), "stl"))
    if not inside(out):
        raise RuntimeError(f"STLs are written only inside {DESIGN_DIR}")
    return run("export_parts.py", scad, "--out", out, "--bed", a.get("bed", "256x256x256"), timeout=900)


def spool_check(summary):
    """Spoolman stock against what the slice needs: each material's grams against the fullest matching spool."""
    if not SPOOLMAN or "grams" not in summary:
        return ""
    spools = spoolman()
    lines = []
    for name, g in zip(summary.get("materials") or [], summary["grams"]):
        kind = next((k for k in ("PETG", "PLA", "ABS", "ASA", "TPU", "PA", "PC") if k in (name or "").upper()), None)
        fits = [s for s in spools if kind and kind in (s["material"] or "").upper()]
        best = max(fits, key=lambda s: s["left"], default=None)
        if not best:
            lines.append(f"{name}: no {kind or 'matching'} spool in Spoolman")
        elif best["left"] < g:
            lines.append(f"{name}: needs {g} g, the fullest {kind} spool (#{best['id']} {best['name']}) has {best['left']} g. Not enough")
        else:
            lines.append(f"{name}: {g} g of {best['left']} g on spool #{best['id']} {best['name']}")
    return "Spool stock (Spoolman):\n" + "\n".join(lines)


def t_slice(a):
    models = [path_in(m, must_exist=True) for m in a["models"]]
    out = path_in(a.get("out") or os.path.join(os.path.dirname(models[0]), "sliced"))
    if not inside(out):
        raise RuntimeError(f"sliced files are written only inside {DESIGN_DIR}")
    args = ["slice", *models, "--printer", a["printer"], "--preset", a.get("preset", "standard"), "--out", out, "--json"]
    if a.get("materials"):
        args += ["--material", ",".join(a["materials"])]
    if a.get("settings"):
        args += ["--set", *[f"{k}={v}" for k, v in a["settings"].items()]]
    text = run("orca.py", *args)
    s = json.loads(text[text.index("{"):])
    if "error" in s:
        raise RuntimeError(s["error"])
    msg = (f"{s['time']}, {s['total_g']} g ({' + '.join(f'{g} g' for g in s['grams'])}), {s.get('changes', 0)} colour changes\n"
           f"{s['printer']} | {s['preset']} | {', '.join(s['materials'])}\n"
           + (f"settings: {', '.join(f'{k}={v}' for k, v in s['settings'].items())}\n" if s.get("settings") else "")
           + f"project (open in OrcaSlicer): {s['project']}\nG-code: {s['gcode']}")
    stock = spool_check(s)
    return msg + ("\n" + stock if stock else "")


def t_open(a):
    models = [path_in(m, must_exist=True) for m in a["models"]]
    args = ["open", *models, "--printer", a["printer"], "--preset", a.get("preset", "standard"),
            "--out", path_in(os.path.join(os.path.dirname(models[0]), "sliced"))]
    if a.get("materials"):
        args += ["--material", ",".join(a["materials"])]
    return run("orca.py", *args)


def t_printers(_):
    return run("orca.py", "printers")


def t_materials(a):
    return run("orca.py", "materials", "--printer", a["printer"])


def t_settings(a):
    return run("orca.py", "settings", *([a["word"]] if a.get("word") else []))


def t_presets(_):
    sys.path.insert(0, TOOLS)
    import orca
    return "\n".join(f"{k}: {w}" for k, w in orca.PRESET_WORDS.items())


def t_fit(_):
    return run("fit.py", "show") + "\n\nFor a design, paste:\n" + run("fit.py", "scad")


def t_fit_set(a):
    args = ["set", "--slide", a["slide"], "--press", a["press"]]
    for k in ("insert", "hinge", "printer"):
        if a.get(k) is not None:
            args += [f"--{k}", a[k]]
    return run("fit.py", *args)


def t_project(a):
    sys.path.insert(0, TOOLS)
    import make_3mf
    parts = [(p.get("label") or os.path.basename(p["stl"]), path_in(p["stl"], must_exist=True), p.get("colour", "#1f1f22"))
             for p in a["parts"]]
    out = path_in(a.get("out") or (a.get("name", "project").replace(" ", "-") + ".3mf"))
    if not inside(out) or not out.lower().endswith(".3mf"):
        raise RuntimeError(f"projects are written only inside {DESIGN_DIR}, as .3mf")
    data, colours = make_3mf.build(parts, a.get("name", "project"), float(a.get("bed", 256)))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, "wb").write(data)
    return f"wrote {out}: {len(parts)} parts, colours {', '.join(colours)}; open it in OrcaSlicer or Bambu Studio"


def spoolman():
    if not SPOOLMAN:
        raise RuntimeError("set SPOOLMAN_URL (e.g. http://192.168.1.20:7912) in this server's env to see spools")
    req = urllib.request.Request(SPOOLMAN + "/api/v1/spool", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = json.load(r)
    except OSError as e:
        raise RuntimeError(f"cannot reach Spoolman at {SPOOLMAN}: {e}")
    out = []
    for s in raw:
        if s.get("archived"):
            continue
        f = s.get("filament") or {}
        out.append({"id": s.get("id"), "left": round(s.get("remaining_weight") or 0), "material": f.get("material") or "",
                    "name": " ".join(x for x in ((f.get("vendor") or {}).get("name"), f.get("name")) if x) or "?",
                    "colour": f.get("color_hex") or ""})
    return out


def t_spools(_):
    rows = [f"#{s['id']:<4} {s['material']:<6} {s['name']:<28} #{s['colour'] or '------'}  {s['left']} g left"
            for s in sorted(spoolman(), key=lambda s: (s["material"], -s["left"]))]
    return "\n".join(rows) or "Spoolman has no active spools"


STR = {"type": "string"}
MODELS = {"type": "array", "items": STR, "description": "STL, 3MF or .scad files; relative names are in the designs folder"}
PRINTER = {"type": "string", "description": "a printer from design_printers (a setup folder name or bambu-p1s)"}
TOOLS_LIST = [
    ("design_save", "Write a text file (an OpenSCAD .scad design, or .json/.txt/.md) into the designs folder. Overwrites.",
     {"path": STR, "content": STR}, t_save, ["path", "content"]),
    ("design_read", "Read a text file (.scad, .json, .txt, .md); relative names are in the designs folder.", {"path": STR}, t_read, ["path"]),
    ("design_files", "List the files in the designs folder.", {}, t_files),
    ("design_look", "Render an OpenSCAD design to one picture with angled, top, front and side views and its real size in mm. Look before exporting.",
     {"path": STR, "part": {"type": "string", "description": "one part = value, e.g. body"},
      "set": {"type": "object", "description": "OpenSCAD variables to override, e.g. {\"wall\": 2}"}, "size": {"type": "integer"}}, t_look, ["path"]),
    ("design_export", "Export every colour part of an OpenSCAD design to its own STL and check each fits the bed.",
     {"path": STR, "out": STR, "bed": {"type": "string", "description": "WxDxH mm, default 256x256x256"}}, t_export, ["path"]),
    ("design_slice", "Slice in OrcaSlicer: print time, grams per filament, colour changes, a 3MF project and G-code. Named settings like infill=30 supports=on; spool stock when Spoolman is set up.",
     {"models": MODELS, "printer": PRINTER, "preset": {"type": "string", "description": "from design_presets, default standard"},
      "materials": {"type": "array", "items": STR, "description": "one per colour, from design_materials, e.g. petg-black"},
      "settings": {"type": "object", "description": "name: value, from design_settings, e.g. {\"infill\": 30, \"supports\": \"on\"}"},
      "out": STR}, t_slice, ["models", "printer"]),
    ("design_open_in_slicer", "Slice and open the result in the OrcaSlicer app on this computer, with its settings.",
     {"models": MODELS, "printer": PRINTER, "preset": STR, "materials": {"type": "array", "items": STR}}, t_open, ["models", "printer"]),
    ("design_printers", "The printers set up for slicing, with their materials.", {}, t_printers),
    ("design_materials", "The filament profiles one printer has.", {"printer": PRINTER}, t_materials, ["printer"]),
    ("design_settings", "OrcaSlicer settings by their plain names (infill, walls, supports, brim, layer...), optionally filtered by a word.",
     {"word": STR}, t_settings),
    ("design_presets", "The slicing presets (draft, standard, strong, solid, fine, low-waste...) and what each does.", {}, t_presets),
    ("design_fit", "The printer's measured fits (slide and press clearance, insert hole, print-in-place hinge gap) and the OpenSCAD lines to use them. Use these, not a material guess.",
     {}, t_fit),
    ("design_fit_set", "Store the fits measured on the fit card, insert card and hinge card.",
     {"slide": {"type": "number"}, "press": {"type": "number"}, "insert": {"type": "number"}, "hinge": {"type": "number"},
      "printer": {"type": "string", "description": "which printer and material these are for"}}, t_fit_set, ["slide", "press"]),
    ("design_project", "Pack STLs into one colour 3MF project (each part its colour and filament slot) for OrcaSlicer or Bambu Studio.",
     {"parts": {"type": "array", "items": {"type": "object", "properties": {"label": STR, "stl": STR, "colour": {"type": "string", "description": "#RRGGBB"}},
                                           "required": ["stl"]}},
      "name": STR, "out": STR, "bed": {"type": "number"}}, t_project, ["parts"]),
    ("design_spools", "Spools in Spoolman with grams left (needs SPOOLMAN_URL).", {}, t_spools),
]
BY_NAME = {t[0]: t for t in TOOLS_LIST}

INSTRUCTIONS = (f"These tools design and slice 3D prints. Designs live in {DESIGN_DIR}. Workflow: write an OpenSCAD design with "
                "design_save (one `part` value per colour, a `// parts:` line, sizes as named variables, clearances from design_fit), "
                "check it with design_look, export with design_export, slice with design_slice (preset and named settings), then "
                "design_project or design_open_in_slicer. Fits come from design_fit, never a guess. Printing is the printer server's job.")


def tool_list():
    out = []
    for t in TOOLS_LIST:
        schema = {"type": "object", "properties": t[2]}
        if len(t) > 4:
            schema["required"] = t[4]
        out.append({"name": t[0], "description": t[1], "inputSchema": schema})
    return out


def handle(msg):
    method = msg.get("method")
    if method == "initialize":
        return {"protocolVersion": (msg.get("params") or {}).get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {}}, "serverInfo": {"name": "design", "version": VERSION}, "instructions": INSTRUCTIONS}
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
        except (Exception, SystemExit) as e:   # one bad call never takes the tools out of the Claude app
            return {"content": [{"type": "text", "text": str(e) or type(e).__name__}], "isError": True}
    if method == "ping":
        return {}
    if msg.get("id") is not None:
        raise LookupError(method)
    return None


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
