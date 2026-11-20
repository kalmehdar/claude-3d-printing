"""OrcaSlicer, driven in plain words: slice with a preset and a few named settings, get time and grams back, open the result in the app.

    python orca.py printers                                  the printer setups it knows
    python orca.py materials --printer u1                    the filaments a printer setup has
    python orca.py settings [word]                           the plain names (infill, walls, supports...) and what they set
    python orca.py slice part.stl --printer u1 --preset strong --material petg-black --set infill=30 supports=on
    python orca.py open  part.stl --printer u1 ...           the same, then opens the sliced project in OrcaSlicer
    python orca.py add-material "Snapmaker PLA Basic @U1" --printer u1 [--as pla-white]
    python orca.py install --printer u1                      puts the printer, its filaments and the presets in OrcaSlicer's own menus

A printer setup is a folder holding machine.json, process.json and filament-<name>.json, as exported from OrcaSlicer
(File > Export > Export preset). --printer takes a folder path, or a name under ~/.config/claude-3d-printing/printers
(CLAUDE_PRINTERS overrides), or bambu-p1s (OrcaSlicer's stock Bambu Lab P1S profile, read from an installed OrcaSlicer).
Models: .stl, .3mf, .step, .obj, or .scad (rendered to STL first with OpenSCAD). A .3mf keeps its colours.
The sliced project (.gcode.3mf style, with the settings inside) and a summary.json go to --out.
OrcaSlicer: ORCA_CLI, or the newest ~/tools/orcaslicer-*/orca-slicer.exe (upstream 2.5 or later). Standard library only.
"""
import argparse
import difflib
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

HOME = os.path.expanduser("~")
PRINTERS = os.environ.get("CLAUDE_PRINTERS") or os.path.join(HOME, ".config", "claude-3d-printing", "printers")

# Plain names for the settings people ask for. Anything else can be set by its OrcaSlicer key (see `settings`).
ALIASES = {
    "layer": ("layer_height", "layer height, mm (0.08 to 0.28 on a 0.4 nozzle)"),
    "infill": ("sparse_infill_density", "infill percent, e.g. 20"),
    "pattern": ("sparse_infill_pattern", "infill pattern: grid, gyroid, cubic, honeycomb, lightning..."),
    "walls": ("wall_loops", "perimeters around every layer; the biggest strength lever"),
    "top": ("top_shell_layers", "solid layers on top"),
    "bottom": ("bottom_shell_layers", "solid layers underneath"),
    "supports": ("enable_support", "on or off"),
    "support-type": ("support_type", "normal(auto) or tree(auto)"),
    "brim": ("brim_type", "auto_brim, outer_only, no_brim"),
    "brim-width": ("brim_width", "mm"),
    "ironing": ("ironing_type", "no ironing, top, topmost, solid"),
    "seam": ("seam_position", "aligned, nearest, back, random"),
    "nozzle-temp": ("nozzle_temperature", "degrees C, every layer after the first"),
    "bed-temp": ("hot_plate_temp", "degrees C (the profile's plate type decides which key the printer uses)"),
    "prime-tower": ("enable_prime_tower", "on or off (multi-colour prints)"),
    "prime-volume": ("prime_volume", "mm3 purged into the tower at each colour change"),
    "tower-width": ("prime_tower_width", "mm"),
}
INSIDE_FLOW = {"set_other_flow_ratios": "1", "inner_wall_flow_ratio": "1.05", "sparse_infill_flow_ratio": "1.05",
               "internal_solid_infill_flow_ratio": "1.05"}
PRESETS = {
    "draft": {"layer_height": "0.28", "wall_loops": "2", "sparse_infill_density": "10%", "top_shell_layers": "3", "bottom_shell_layers": "3"},
    "standard": {},
    # strong/solid follow Maker's Muse, "Incredibly strong 3D printing settings using OrcaSlicer 2.3.2" (Apr 2026):
    # an extra wall on every other layer so the walls interlock, wider inner walls, and a little more plastic inside only,
    # so layers fuse harder while the outer wall (the look) is untouched.
    # ponytail: 0.5 mm and 1.05 are starting values, not measured on Khalid's printers; tune after a break test.
    "strong": {"wall_loops": "4", "alternate_extra_wall": "1", "sparse_infill_density": "40%", "sparse_infill_pattern": "gyroid",
               "top_shell_layers": "5", "bottom_shell_layers": "5", "inner_wall_line_width": "0.5", **INSIDE_FLOW},
    "solid": {"wall_loops": "4", "sparse_infill_density": "100%", "sparse_infill_pattern": "concentric",
              "internal_solid_infill_pattern": "concentric", "top_shell_layers": "5", "bottom_shell_layers": "5",
              "inner_wall_line_width": "0.5", **INSIDE_FLOW},
    "fine": {"layer_height": "0.12", "wall_loops": "3", "top_shell_layers": "7", "bottom_shell_layers": "5"},
    # multi-colour on a tool changer: each head keeps its own colour, so most of the default purge is waste
    "low-waste": {"prime_volume": "20", "prime_tower_width": "20", "wipe_tower_no_sparse_layers": "1"},
}
PRESET_KEYS = {k for p in PRESETS.values() for k in p}
PRESET_WORDS = {"draft": "fast and rough: 0.28 mm, 2 walls, 10% infill", "standard": "the printer's own profile, unchanged",
                "strong": "for parts that take load: 4 walls plus an extra one every other layer, 40% gyroid, thick top and bottom, inside walls and infill fused harder",
                "solid": "the strongest: like strong, but 100% concentric infill (slow and heavy; brackets, hooks, clamps)", "fine": "smooth: 0.12 mm layers, 3 walls",
                "low-waste": "multi-colour on a tool changer: smaller prime tower and purge (check the first print for colour bleed)"}


def find(env, patterns):
    if os.environ.get(env):
        return os.environ[env]
    for p in patterns:
        hits = sorted(glob.glob(os.path.join(HOME, "tools", p), recursive=True), reverse=True)
        if hits:
            return hits[0]
    sys.exit(f"set {env}: nothing found for {patterns}")


def orca_cli():
    return find("ORCA_CLI", ["orcaslicer-*/orca-slicer.exe", "OrcaSlicer*/orca-slicer.exe"])


def vendor_dirs():
    """Every system profile folder of every OrcaSlicer build under ~/tools (Bambu, Snapmaker, Prusa...)."""
    return sorted(glob.glob(os.path.join(HOME, "tools", "**", "resources", "profiles", "*", ""), recursive=True))


def flatten(folder, name, kind):
    """Follow a system profile's "inherits" chain inside one vendor folder and merge it into one complete profile."""
    index = {}
    for f in glob.glob(os.path.join(folder, kind, "*.json")):
        try:
            index[json.load(open(f, encoding="utf-8")).get("name")] = f
        except (ValueError, OSError):
            pass
    chain, n = [], name
    while n:
        if n not in index:
            sys.exit(f"no {kind} profile named {n!r} in {folder}")
        d = json.load(open(index[n], encoding="utf-8"))
        chain.append(d)
        n = d.get("inherits")
    out = {}
    for d in reversed(chain):
        out.update(d)
    out.pop("inherits", None)
    out["name"] = name
    return out


def bambu(material, tmp):
    """OrcaSlicer's stock Bambu Lab P1S 0.4 profile as a printer setup folder (written to tmp)."""
    base = os.environ.get("ORCA_PROFILES") or next((d for d in vendor_dirs() if os.path.basename(d.rstrip("\\/")) == "BBL"
                                                     and glob.glob(os.path.join(d, "machine", "*.json"))), None)   # 2.5 packs BBL in a .opc
    if not base:
        sys.exit("no OrcaSlicer BBL profiles under ~/tools: set ORCA_PROFILES")
    mat = "PETG" if "PETG" in material.upper() else "PLA"
    files = {"machine": flatten(base, "Bambu Lab P1S 0.4 nozzle", "machine"),
             "process": flatten(base, "0.20mm Standard @BBL X1C", "process"),
             f"filament-{mat.lower()}": flatten(base, f"Generic {mat}", "filament")}
    files["process"]["curr_bed_type"] = "Textured PEI Plate"   # the default Cool Plate refuses PETG
    for k, d in files.items():
        d["from"] = "system"
        json.dump(d, open(os.path.join(tmp, k + ".json"), "w", encoding="utf-8"))
    return tmp


def printer_dir(name, tmp=None, material=""):
    if name == "bambu-p1s":
        return bambu(material, tmp or tempfile.mkdtemp(prefix="orca-p1s-"))
    for d in (name, os.path.join(PRINTERS, name)):
        if os.path.isfile(os.path.join(d, "machine.json")):
            return d
    sys.exit(f"no printer setup {name!r}: give a folder with machine.json, process.json and filament-*.json, "
             f"or put one in {PRINTERS}{os.sep}{name}")


def materials(folder):
    return {os.path.basename(f)[9:-5]: f for f in sorted(glob.glob(os.path.join(folder, "filament-*.json")))}


def pick_materials(folder, wanted, slots):
    """wanted: comma list of material names or parts of names (petg-black, pla, red). One per colour; the last repeats."""
    have = materials(folder)
    if not have:
        sys.exit(f"no filament-*.json in {folder}")
    picks = []
    for w in (wanted.split(",") if wanted else [next(iter(have))]):
        w = w.strip().lower()
        hit = [n for n in have if n == w] or [n for n in have if w in n]
        if not hit:
            sys.exit(f"no material {w!r} in {folder}; it has: {', '.join(have)}")
        picks.append(have[hit[0]])
    return (picks + [picks[-1]] * slots)[:max(slots, len(picks))]


def slots_in(model):
    """How many filaments a .3mf asks for (its parts' extruder numbers); 1 for anything else."""
    if not model.lower().endswith(".3mf"):
        return 1
    try:
        z = zipfile.ZipFile(model)
        text = "".join(z.read(n).decode("utf-8", "replace") for n in z.namelist() if n.endswith((".config", ".model")) and "Metadata" in n)
    except (zipfile.BadZipFile, OSError):
        return 1
    found = [int(x) for x in re.findall(r'key="extruder" value="(\d+)"', text)]
    return max(found or [1])


def parse_sets(pairs, keys):
    """infill=30 supports=on walls=4 layer_height=0.16 -> OrcaSlicer keys and string values, checked against the profiles."""
    out = {}
    for p in pairs:
        if "=" not in p:
            sys.exit(f"--set takes name=value, not {p!r}")
        k, v = (s.strip() for s in p.split("=", 1))
        key = ALIASES.get(k.lower(), (k, ""))[0]
        if key not in keys:
            near = difflib.get_close_matches(key, list(keys) + list(ALIASES), n=4)
            sys.exit(f"unknown setting {k!r}" + (f"; did you mean {', '.join(near)}?" if near else "; see `orca.py settings`"))
        if v.lower() in ("on", "yes", "true"):
            v = "1"
        elif v.lower() in ("off", "no", "false"):
            v = "0"
        if key.endswith("_density") and not v.endswith("%"):
            v += "%"
        out[key] = v
    return out


def apply(profile, overrides, add=False):
    for k, v in overrides.items():
        if k in profile or add:
            profile[k] = [v] * len(profile[k]) if isinstance(profile.get(k), list) else v


def scad_to_stl(path, tmp):
    exe = os.environ.get("OPENSCAD") or shutil.which("openscad") or next(
        (c for c in (r"C:\Program Files\OpenSCAD\openscad.com",) + tuple(glob.glob(os.path.join(HOME, "tools", "openscad-*", "**", "openscad.com"), recursive=True)) if os.path.exists(c)), None)
    if not exe:
        sys.exit("OpenSCAD not found: set OPENSCAD")
    out = os.path.join(tmp, os.path.splitext(os.path.basename(path))[0] + ".stl")
    r = subprocess.run([exe, "-o", out, path], capture_output=True, text=True)
    if r.returncode or not os.path.exists(out):
        sys.exit("OpenSCAD failed: " + (r.stderr or r.stdout)[-800:])
    return out


def stats(gcode_text):
    t = re.search(r"; estimated printing time \(normal mode\) = (.+)", gcode_text) or re.search(r"total estimated time: ([^;\n]+)", gcode_text)
    g = re.search(r"; filament used \[g\] = (.+)", gcode_text)
    c = re.search(r"; total filament change = (\d+)", gcode_text)
    if not (t and g):
        return {"error": "no time or grams in the G-code"}
    grams = [round(float(x), 1) for x in g.group(1).split(",")]
    return {"time": t.group(1).strip(), "grams": grams, "total_g": round(sum(grams), 1), "changes": int(c.group(1)) if c else 0}


def slice_(models, printer, preset="standard", material="", sets=(), out=None, timeout=900):
    """Slice; returns the summary dict (also written to out/summary.json)."""
    out = os.path.abspath(out or tempfile.mkdtemp(prefix="orca-"))
    os.makedirs(out, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "p"))
        folder = printer_dir(printer, os.path.join(tmp, "p"), material)
        models = [scad_to_stl(m, tmp) if m.lower().endswith(".scad") else os.path.abspath(m) for m in models]
        for m in models:
            if not os.path.exists(m):
                sys.exit(f"no such model: {m}")
        machine = json.load(open(os.path.join(folder, "machine.json"), encoding="utf-8"))
        process = json.load(open(os.path.join(folder, "process.json"), encoding="utf-8"))
        fils = pick_materials(folder, material, max(slots_in(m) for m in models))
        fil_data = [json.load(open(f, encoding="utf-8")) for f in fils]
        if preset not in PRESETS:
            sys.exit(f"no preset {preset!r}: {', '.join(PRESETS)}")
        keys = set(machine) | set(process) | {k for d in fil_data for k in d}
        overrides = {**PRESETS[preset], **parse_sets(sets, keys | PRESET_KEYS)}
        if overrides.get("alternate_extra_wall") == "1" and overrides.get("sparse_infill_pattern", process.get("sparse_infill_pattern")) == "lightning":
            overrides["alternate_extra_wall"] = "0"   # Orca: the extra wall breaks with lightning infill
            print("alternate extra wall turned off: it does not work with lightning infill", file=sys.stderr)
        for d in [machine, process] + fil_data:
            apply(d, overrides)
        apply(process, {k: v for k, v in overrides.items() if k not in keys}, add=True)   # Orca defaults the flattened profile leaves out
        paths = []
        for i, d in enumerate([machine, process] + fil_data):
            p = os.path.join(tmp, f"{i}.json")
            json.dump(d, open(p, "w", encoding="utf-8"))
            paths.append(p)
        name = os.path.splitext(os.path.basename(models[0]))[0]
        for old in glob.glob(os.path.join(out, "*.gcode")):
            os.remove(old)
        cmd = [orca_cli(), "--slice", "0", "--load-settings", f"{paths[0]};{paths[1]}", "--load-filaments", ";".join(paths[2:]),
               "--outputdir", out, "--export-3mf", f"{name}-sliced.3mf"] + models
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            sys.exit(f"OrcaSlicer ran past {timeout // 60} minutes")
    gcode = glob.glob(os.path.join(out, "*.gcode"))
    s = stats(open(gcode[0], encoding="utf-8", errors="replace").read()) if gcode else {
        "error": "OrcaSlicer wrote nothing: " + " ".join((r.stdout + r.stderr).split())[-600:] + " (open the model in the app to see why)"}
    s.update({"printer": machine.get("name", printer), "preset": preset, "materials": [d.get("name") for d in fil_data],
              "settings": overrides, "project": os.path.join(out, f"{name}-sliced.3mf"), "gcode": gcode[0] if gcode else None})
    json.dump(s, open(os.path.join(out, "summary.json"), "w", encoding="utf-8"), indent=1)
    return s


def report(s):
    if "error" in s:
        print("FAILED:", s["error"])
        return
    grams = " + ".join(f"{g} g" for g in s["grams"])
    print(f"{s['time']}, {s['total_g']} g" + (f" ({grams}, {s['changes']} colour changes)" if len(s["grams"]) > 1 else ""))
    print(f"{s['printer']} | {s['preset']} | {', '.join(s['materials'])}")
    if s["settings"]:
        print("settings: " + ", ".join(f"{k}={v}" for k, v in s["settings"].items()))
    print("project:", s["project"])
    print("gcode:  ", s["gcode"])


def open_app(path):
    exe = os.environ.get("ORCA_APP") or orca_cli()
    subprocess.Popen([exe, path], close_fds=True, creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    print("opened in OrcaSlicer:", path)


def add_material(system_name, printer, as_name=None):
    """system_name: a full OrcaSlicer filament name, or part of one ("Snapmaker TPU"); only ones made for this printer count."""
    folder = printer_dir(printer)
    machine = json.load(open(os.path.join(folder, "machine.json"), encoding="utf-8"))
    found = {}   # name -> vendor folder, for every filament this printer can use
    for v in vendor_dirs():
        for f in glob.glob(os.path.join(v, "filament", "*.json")):
            try:
                d = json.load(open(f, encoding="utf-8"))
            except (ValueError, OSError):
                continue
            if d.get("name") and d.get("instantiation") != "false" and machine["name"] in (d.get("compatible_printers") or [machine["name"]]):
                found.setdefault(d.get("name"), v)
    hits = [system_name] if system_name in found else [n for n in found if system_name.lower() in n.lower()]
    if len(hits) != 1:
        sys.exit((f"{len(hits)} filaments match {system_name!r} for {machine['name']}; say which:\n  " + "\n  ".join(sorted(hits)))
                 if hits else f"no filament matching {system_name!r} for {machine['name']} in any OrcaSlicer under ~/tools")
    system_name = hits[0]
    d = flatten(found[system_name], system_name, "filament")
    d.update({"from": "User", "compatible_printers": [machine["name"]], "filament_settings_id": [system_name]})
    slug = as_name or re.sub(r"[^a-z0-9]+", "-", system_name.split("@")[0].lower()).strip("-")
    path = os.path.join(folder, f"filament-{slug}.json")
    json.dump(d, open(path, "w", encoding="utf-8", newline="\n"), indent=1)
    print(f"added {system_name} as {slug}: {path}")


def install(printer, user_dir=None):
    """Copy a printer setup into OrcaSlicer's user presets, with the Claude presets as processes beside it."""
    folder = printer_dir(printer)
    user_dir = user_dir or os.path.join(os.environ.get("APPDATA", HOME), "OrcaSlicer", "user", "default")
    machine = json.load(open(os.path.join(folder, "machine.json"), encoding="utf-8"))
    process = json.load(open(os.path.join(folder, "process.json"), encoding="utf-8"))
    mname = machine["name"]
    wrote = []
    try:   # a user preset with no version, or one newer than the app, is refused at start-up
        conf = open(os.path.join(os.path.dirname(os.path.dirname(user_dir)), "OrcaSlicer.conf"), encoding="utf-8").read()
        version = re.search(r'"version":\s*"([\d.]+)"', conf).group(1)   # the conf ends in a checksum line, so not json.load
    except (OSError, AttributeError):
        version = "2.0.0"

    def put(kind, d, name):
        d = dict(d, name=name, version=version, **{"from": "User", "inherits": ""})
        for k in ("filament_colour", "curr_bed_type"):   # project keys, not preset keys: the app drops them with a warning
            d.pop(k, None)
        if kind != "machine":
            d["compatible_printers"] = [mname]
        os.makedirs(os.path.join(user_dir, kind), exist_ok=True)
        p = os.path.join(user_dir, kind, re.sub(r'[<>:"/\\|?*]', "_", name) + ".json")
        json.dump(d, open(p, "w", encoding="utf-8"), indent=1)
        wrote.append(p)

    built_in = any(mname == json.load(open(f, encoding="utf-8")).get("name")
                   for v in vendor_dirs() for f in glob.glob(os.path.join(v, "machine", "*.json")))
    if not built_in:   # the app already ships most printers; a second copy of the same name is skipped at start-up
        put("machine", machine, mname)
    for preset, o in PRESETS.items():
        d = dict(process)
        apply(d, o, add=True)
        put("process", d, f"Claude {preset} @{mname}")
    for slug, f in materials(folder).items():
        put("filament", json.load(open(f, encoding="utf-8")), f"Claude {slug} @{mname}")
    print(f"{len(wrote)} presets in {user_dir}; restart OrcaSlicer and pick printer {mname!r}, then the 'Claude ...' presets")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0], formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("printers")
    m = sub.add_parser("materials")
    m.add_argument("--printer", required=True)
    st = sub.add_parser("settings")
    st.add_argument("word", nargs="?")
    st.add_argument("--printer")
    for c in ("slice", "open"):
        s = sub.add_parser(c)
        s.add_argument("models", nargs="+")
        s.add_argument("--printer", required=True)
        s.add_argument("--preset", default="standard", choices=list(PRESETS))
        s.add_argument("--material", default="", help="comma list, one per colour: petg-black,petg-red")
        s.add_argument("--set", nargs="*", default=[], metavar="NAME=VALUE")
        s.add_argument("--out")
        s.add_argument("--json", action="store_true", help="print the summary as JSON")
    am = sub.add_parser("add-material")
    am.add_argument("system_name")
    am.add_argument("--printer", required=True)
    am.add_argument("--as", dest="as_name")
    ins = sub.add_parser("install")
    ins.add_argument("--printer", required=True)
    ins.add_argument("--user-dir", help="OrcaSlicer user preset folder (default: %%APPDATA%%/OrcaSlicer/user/default)")
    a = ap.parse_args()

    if a.cmd == "printers":
        names = sorted(os.listdir(PRINTERS)) if os.path.isdir(PRINTERS) else []
        for n in names + ["bambu-p1s"]:
            print(n, "" if n == "bambu-p1s" else f"({', '.join(materials(os.path.join(PRINTERS, n))) or 'no filaments'})")
        if not names:
            print(f"(add your own: a folder per printer in {PRINTERS})")
    elif a.cmd == "materials":
        for n, f in materials(printer_dir(a.printer)).items():
            print(f"{n:<20} {json.load(open(f, encoding='utf-8')).get('name')}")
    elif a.cmd == "settings":
        for k, (key, doc) in ALIASES.items():
            if not a.word or a.word in k or a.word in key:
                print(f"{k:<13} {key:<24} {doc}")
        for k, w in PRESET_WORDS.items():
            if not a.word or a.word in k:
                print(f"--preset {k:<9} {w}")
        if a.word and a.printer:
            folder = printer_dir(a.printer)
            keys = set()
            for f in [os.path.join(folder, "machine.json"), os.path.join(folder, "process.json")] + list(materials(folder).values()):
                keys |= set(json.load(open(f, encoding="utf-8")))
            for k in sorted(k for k in keys if a.word in k):
                print(f"  {k}")
    elif a.cmd in ("slice", "open"):
        s = slice_(a.models, a.printer, a.preset, a.material, a.set, a.out)
        print(json.dumps(s, indent=1)) if a.json else report(s)
        if "error" in s:
            sys.exit(1)
        if a.cmd == "open":
            open_app(s["project"])
    elif a.cmd == "add-material":
        add_material(a.system_name, a.printer, a.as_name)
    elif a.cmd == "install":
        install(a.printer, a.user_dir)


if __name__ == "__main__":
    main()
