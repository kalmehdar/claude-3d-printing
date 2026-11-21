"""c3dp: one command for every claude-3d-printing tool.

    c3dp doctor                       what is installed and set up: OpenSCAD, OrcaSlicer, Blender, fits, printers, Spoolman
    c3dp setup                        put a `c3dp` launcher on your PATH (~/.local/bin, or %USERPROFILE%\\.local\\bin)

  design
    c3dp look model.scad [--part p]   one picture with four views and the real size           (look.py)
    c3dp export model.scad            every colour part to its own STL, checked against the bed (export_parts.py)
    c3dp audit stl/ [--bed 256]       mesh check in Blender: holes, inside-out faces, thin walls (mesh_audit.py)
    c3dp render model.scad            studio render                                            (render.py)
    c3dp 3mf ...                      colour 3MF project for OrcaSlicer or Bambu Studio        (make_3mf.py)
    c3dp bench ...                    Print Bench page                                         (print_bench.py)
    c3dp fit show|scad|set ...        your printer's measured fits                             (fit.py)

  slice                               (orca.py: printers, materials, settings, slice, open, add-material, install,
    c3dp slice part.stl --printer u1 --preset strong --set infill=30         new-printer, adopt-gcode)
    c3dp stats bench.json --printer u1 --profiles <dir>                      (slice_stats.py)
    c3dp spools                       Spoolman spools and grams left (SPOOLMAN_URL)

  print
    c3dp printer status|snap|send|start|watch ...                            (printer.py)
    c3dp workshop --printer <ip>      the Workshop page                                       (workshop.py)

  Claude
    c3dp mcp design                   the design-and-slice MCP server  (claude mcp add design -- c3dp mcp design)
    c3dp mcp printer                  the printer MCP server           (needs WORKSHOP_URL)

Every command takes the same arguments as the script it runs; `c3dp <command> --help` shows them. Standard library only.
"""
import glob
import json
import os
import shutil
import subprocess
import sys

TOOLS = os.path.dirname(os.path.abspath(__file__))
ORCA_COMMANDS = {"printers", "materials", "settings", "slice", "open", "add-material", "install", "new-printer", "adopt-gcode"}
SCRIPTS = {"look": "look.py", "export": "export_parts.py", "render": "render.py", "3mf": "make_3mf.py", "bench": "print_bench.py",
           "fit": "fit.py", "stats": "slice_stats.py", "printer": "printer.py", "workshop": "workshop.py"}
MCP = {"design": "design_mcp.py", "printer": "printer_mcp.py"}


def script(name, args):
    return subprocess.call([sys.executable, os.path.join(TOOLS, name), *args])


def blender():
    if os.environ.get("BLENDER"):
        return os.environ["BLENDER"]
    found = shutil.which("blender")
    if found:
        return found
    hits = sorted(glob.glob(r"C:\Program Files\Blender Foundation\Blender*\blender.exe")) + ["/Applications/Blender.app/Contents/MacOS/Blender"]
    return next((h for h in reversed(hits) if os.path.exists(h)), None)


def doctor(as_json):
    sys.path.insert(0, TOOLS)
    import fit
    import orca
    checks = []

    def check(name, ok, detail):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    check("python", sys.version_info >= (3, 8), sys.version.split()[0])
    scad = os.environ.get("OPENSCAD") or shutil.which("openscad") or next(iter(glob.glob(os.path.join(orca.HOME, "tools", "openscad-*", "**", "openscad.com"), recursive=True)), None) \
        or next((p for p in (r"C:\Program Files\OpenSCAD\openscad.com", "/Applications/OpenSCAD.app/Contents/MacOS/OpenSCAD") if os.path.exists(p)), None)
    check("OpenSCAD", scad, scad or "not found: install it, or set OPENSCAD")
    try:
        cli = orca.orca_cli()
        check("OrcaSlicer", cli and os.path.exists(cli), cli)
    except SystemExit as e:
        check("OrcaSlicer", False, str(e) or "not found: install OrcaSlicer 2.5 or later, or set ORCA_CLI")
    b = blender()
    check("Blender (mesh audit, renders)", b, b or "not found: optional; set BLENDER")
    f = fit.load()
    check("measured fits", f["measured"], ("measured: " if f["measured"] else "defaults, print examples/models/fit-card.scad: ")
          + ", ".join(f"{k} {f[k]}" for k in ("slide", "press", "insert", "hinge") if k in f))
    names = sorted(os.listdir(orca.PRINTERS)) if os.path.isdir(orca.PRINTERS) else []
    check("printer setups for slicing", names, ", ".join(names) + " (plus bambu-p1s)" if names else f"only bambu-p1s; add yours with c3dp new-printer (folder {orca.PRINTERS})")
    pj = os.path.join(orca.HOME, ".config", "claude-3d-printing", "printer.json")
    check("printer link", os.path.exists(pj), pj if os.path.exists(pj) else "no printer.json: c3dp printer works once a printer address is set (see printer.py --help)")
    sm = os.environ.get("SPOOLMAN_URL")
    check("Spoolman", sm, sm or "not set: optional, SPOOLMAN_URL=http://<host>:7912")
    if as_json:
        print(json.dumps(checks, indent=1))
    else:
        for c in checks:
            print(f"{'ok ' if c['ok'] else '-- '} {c['check']:<30} {c['detail']}")
    return 0 if all(c["ok"] for c in checks[:3]) else 1   # python, OpenSCAD and OrcaSlicer are the ones that matter


def setup():
    home = os.path.expanduser("~")
    bindir = os.path.join(home, ".local", "bin")
    os.makedirs(bindir, exist_ok=True)
    me = os.path.abspath(__file__)
    if os.name == "nt":
        path = os.path.join(bindir, "c3dp.cmd")
        body = f'@echo off\r\n"{sys.executable}" "{me}" %*\r\n'
    else:
        path = os.path.join(bindir, "c3dp")
        body = f'#!/bin/sh\nexec "{sys.executable}" "{me}" "$@"\n'
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(body)
    if os.name != "nt":
        os.chmod(path, 0o755)
    on_path = any(os.path.normcase(os.path.abspath(p)) == os.path.normcase(bindir) for p in os.environ.get("PATH", "").split(os.pathsep) if p)
    print(f"wrote {path}" + ("" if on_path else f"\nadd {bindir} to your PATH to run `c3dp` from anywhere"))
    return 0


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "doctor":
        return doctor("--json" in rest)
    if cmd == "setup":
        return setup()
    if cmd in ORCA_COMMANDS:
        return script("orca.py", [cmd, *rest])
    if cmd in SCRIPTS:
        return script(SCRIPTS[cmd], rest)
    if cmd == "audit":
        b = blender()
        if not b:
            sys.exit("Blender not found: install it or set BLENDER")
        return subprocess.call([b, "--background", "--factory-startup", "--python", os.path.join(TOOLS, "mesh_audit.py"), "--", *rest])
    if cmd == "spools":
        sys.path.insert(0, TOOLS)
        import design_mcp
        try:
            print(design_mcp.t_spools({}))
        except RuntimeError as e:
            sys.exit(str(e))
        return 0
    if cmd == "mcp":
        if not rest or rest[0] not in MCP:
            sys.exit("c3dp mcp design | printer")
        return script(MCP[rest[0]], rest[1:])
    sys.exit(f"unknown command {cmd!r}; `c3dp --help` lists them")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
