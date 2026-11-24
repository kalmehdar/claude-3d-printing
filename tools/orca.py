"""OrcaSlicer, driven in plain words: slice with a preset and a few named settings, get time and grams back, open the result in the app.

    python orca.py printers                                  the printer setups it knows
    python orca.py materials --printer u1                    the filaments a printer setup has
    python orca.py settings [word]                           the plain names (infill, walls, supports...) and what they set
    python orca.py slice part.stl --printer u1 --preset strong --material petg-black --set infill=30 supports=on
    python orca.py open  part.stl --printer u1 ...           the same, then opens the sliced project in OrcaSlicer
    python orca.py add-material "Snapmaker PLA Basic @U1" --printer u1 [--as pla-white]
    python orca.py install --printer u1                      puts the printer, its filaments and the presets in OrcaSlicer's own menus
    python orca.py new-printer u1 --vendor Snapmaker --machine "Snapmaker U1 (0.4 nozzle)" --process "0.20mm Standard @Snapmaker U1 (0.4 nozzle)" \
        --filament "Snapmaker PLA SnapSpeed @U1:pla-snapspeed"   a setup from a slicer's bundled vendor profiles
    python orca.py adopt-gcode good-print.gcode --printer u1  take the start/end/tool-change G-code a known-good print was sliced with

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
    "wall-speed": ("outer_wall_speed", "outer wall speed, mm/s (slower can show fewer lines; smooth uses 80)"),
    "wall-accel": ("outer_wall_acceleration", "outer wall acceleration, mm/s2 (smooth uses 3000)"),
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
    # quality: same 0.2 mm layers, the outside printed gently so walls, corners and text come out clean, for 3-12% more time (fit card +12%, all settings together).
    # Measured 21 Nov on the U1 profile (fit card, gridfinity bin): slower outer walls, Arachne, precise outer wall, seam at the
    # back and a third wall each cost 0-6%; ironing alone cost 12-37%, so it lives in "polished".
    # ponytail: 80 mm/s and 3000 mm/s2 are common PETG starting values, not measured on the U1; tune after a seam/corner test.
    "quality": {"outer_wall_speed": "80", "outer_wall_acceleration": "3000", "top_surface_speed": "100",
                "wall_loops": "3", "wall_generator": "arachne", "precise_outer_wall": "1", "seam_position": "back",
                # scarf seam (the seam ramps in over 20 mm instead of a bump), staggered inner seams and the outer wall printed
                # between two inner walls: four of ten videos found scarf works, none against (22 Nov, youtube-smooth-surfaces.md)
                # 22 Nov seam test on the U1 (A scarf, B scarf at half speed, C plain at the back): Khalid picked C, so scarf is off
                "seam_slope_type": "none", "seam_slope_conditional": "1",
                "seam_slope_inner_walls": "1", "seam_slope_steps": "20", "seam_slope_entire_loop": "1", "staggered_inner_seams": "1",
                "wall_sequence": "inner-outer-inner wall"},
    # lean: quality's clean outside, less plastic inside. Measured 22 Nov on the U1 profile (test card, clip-on tags, ProDesk
    # sleeve, X9 cradle): about standard's time, 11-30% less filament. The tool changer's prime at each head change was the
    # biggest waste (45 mm3 a change; flushing does nothing on separate nozzles); lightning infill only holds up the tops,
    # so lean is for covers, labels, cases and decorative parts, not brackets or anything that takes a load (use strong).
    # prime 20 mm3 (Khalid, 22 Nov: 20 by default, 45 when white sits beside other colours; Workshop raises it for pale parts).
    # smooth: the best-looking walls we can print (Khalid, 22 Nov: "proper high quality smooth prints without visible lines").
    # quality's outside (gentle outer wall, Arachne, scarf seam round the whole loop, inner-outer-inner) on 0.12 mm layers,
    # which makes each line about 40% smaller. ponytail: 0.12 is the finest the videos found worth it on a 0.4 nozzle; 0.08
    # fused supports into the model (TKMakes). Even layers matter more once the filament is dried and calibrated.
    # 23 Nov wall tests on the U1 (black SnapSpeed PLA, 30 mm part, lamp across the walls): 0.08 mm layers were the
    # smoothest; 1500 outer-wall acceleration beat 3000 and a 45 mm/s outer wall at 0.12. Top and bottom layer counts
    # keep the skins about as thick as before (0.8 / 0.56 mm). Supports can fuse at 0.08: use quality for supported parts.
    "smooth": {"layer_height": "0.08", "wall_loops": "3", "top_shell_layers": "10", "bottom_shell_layers": "7",
               "outer_wall_speed": "80", "outer_wall_acceleration": "1500", "top_surface_speed": "100",
               "wall_generator": "arachne", "precise_outer_wall": "1", "seam_position": "back",
               "seam_slope_type": "none", "seam_slope_conditional": "1", "seam_slope_inner_walls": "1",
               "seam_slope_steps": "20", "seam_slope_entire_loop": "1", "staggered_inner_seams": "1",
               "wall_sequence": "inner-outer-inner wall"},
    # figure: the default for figurines and painted models (Khalid, 24 Nov: "keep improving the quality... smoother defaults").
    # quality's clean outside on 0.16 mm layers with the 23 Nov wall-test outer wall (45 mm/s, 1500 acceleration), tree
    # supports on. 0.16 not 0.12: the duo hoodie ghost at 0.12 was 12.4 h against 9.2 h, and 0.08 fuses supports. Multi-colour
    # prints add the tower and support-contact sets on top (wipe_tower_filament, support_interface_filament).
    "figure": {"layer_height": "0.16", "wall_loops": "3", "top_shell_layers": "6", "bottom_shell_layers": "4",
               "outer_wall_speed": "45", "outer_wall_acceleration": "1500", "top_surface_speed": "100",
               "wall_generator": "arachne", "precise_outer_wall": "1", "seam_position": "back",
               "seam_slope_type": "none", "staggered_inner_seams": "1", "wall_sequence": "inner-outer-inner wall",
               "enable_support": "1", "support_type": "tree(auto)"},
    "lean": {"outer_wall_speed": "80", "outer_wall_acceleration": "3000", "top_surface_speed": "100", "wall_generator": "arachne",
             "precise_outer_wall": "1", "seam_position": "back", "prime_volume": "20",
             "sparse_infill_pattern": "lightning", "sparse_infill_density": "10%"},
    "polished": {"outer_wall_speed": "80", "outer_wall_acceleration": "3000", "top_surface_speed": "100",
                 "wall_loops": "3", "wall_generator": "arachne", "precise_outer_wall": "1", "seam_position": "back",
                 "seam_slope_type": "none", "seam_slope_conditional": "1",
                 "seam_slope_inner_walls": "1", "seam_slope_steps": "20", "seam_slope_entire_loop": "1", "staggered_inner_seams": "1",
                 "wall_sequence": "inner-outer-inner wall",
                 # ironing 25% flow, 0.15 mm spacing, 40 mm/s: SUNLU PETG, two rounds on 22 Nov. Round 1 picked 20% of 10-40%;
                 # round 2 (labelled A-D, Khalid's pick A and B) showed 20 mm/s leaves diagonal streaks and 0.08 spacing adds
                 # nothing over 0.15. Filament not yet dried. ponytail: retest after drying.
                 "ironing_type": "top", "ironing_speed": "40", "ironing_flow": "25%", "ironing_spacing": "0.15"},
    # multi-colour on a tool changer: each head keeps its own colour, so most of the default purge is waste
    "low-waste": {"prime_volume": "20", "prime_tower_width": "20", "wipe_tower_no_sparse_layers": "1"},
    # grip: fuzzy skin on the outside, for handles and anything held (the default is too coarse to look good; 10 Ways to Hide
    # Layer Lines, Nov 2026). ponytail: 0.2 mm thickness and 0.4 mm point distance are a common gentle start, not tested on the U1.
    # fine-text: raised lettering and small detail (How to Get Razor Sharp Text, Nov 2026): thin lines, fine layers, more
    # walls and shells, about half speed. ponytail: the video's numbers on a Bambu; check the first lettered print on the U1.
    "fine-text": {"layer_height": "0.12", "line_width": "0.25", "outer_wall_line_width": "0.25", "inner_wall_line_width": "0.25",
                  "top_surface_line_width": "0.25", "wall_loops": "4", "top_shell_layers": "6", "bottom_shell_layers": "6",
                  "outer_wall_speed": "60", "inner_wall_speed": "90", "top_surface_speed": "60", "detect_thin_wall": "1"},
    "grip": {"fuzzy_skin": "external", "fuzzy_skin_thickness": "0.2", "fuzzy_skin_point_distance": "0.4", "wall_loops": "3"},
}
PRESET_KEYS = {k for p in PRESETS.values() for k in p}
PRESET_WORDS = {"draft": "fast and rough: 0.28 mm, 2 walls, 10% infill", "standard": "the printer's own profile, unchanged",
                "strong": "for parts that take load: 4 walls plus an extra one every other layer, 40% gyroid, thick top and bottom, inside walls and infill fused harder",
                "solid": "the strongest: like strong, but 100% concentric infill (slow and heavy; brackets, hooks, clamps)", "fine": "smooth: 0.12 mm layers, 3 walls",
                "quality": "clean walls and parts that fit for 3-12% more time: gentle outer walls, 3 walls, Arachne, precise outer wall, seam at the back",
                "smooth": "the smoothest walls: quality's clean outside on 0.08 mm layers and a gentler outer wall (tested 23 Nov); about half again slower than 0.12; not for parts with supports",
                "polished": "quality plus ironed flat tops (20% flow, the best of our test) (plaques, tags, lids): 12-40% more time",
                "lean": "clean outside, light inside: about standard's time and 11-30% less filament (covers, labels, cases; not load-bearing parts)",
                "low-waste": "multi-colour on a tool changer: smaller prime tower and purge (check the first print for colour bleed)",
                "grip": "a gentle fuzzy skin outside, for handles and things you hold; hides layer lines (not for faces that must fit)",
                "figure": "figurines and painted models: 0.16 mm layers, the gentle outer wall from the 23 Nov test, tree supports on (about a third slower than quality)",
                "fine-text": "sharp lettering and fine detail: 0.12 mm layers, 0.25 mm lines, 4 walls, about half speed (slow; make the text bold and print it face up)"}


def find(env, patterns):
    if os.environ.get(env):
        return os.environ[env]
    for p in patterns:
        hits = sorted(glob.glob(os.path.join(HOME, "tools", p), recursive=True), reverse=True)
        if hits:
            return hits[0]
    sys.exit(f"set {env}: nothing found for {patterns}")


MAC_ORCA = "/Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer"


def orca_cli():
    if not os.environ.get("ORCA_CLI") and os.path.exists(MAC_ORCA):
        return MAC_ORCA
    return find("ORCA_CLI", ["orcaslicer-*/orca-slicer.exe", "OrcaSlicer*/orca-slicer.exe"])


def vendor_dirs():
    """Every system profile folder of every OrcaSlicer build under ~/tools or in /Applications on a Mac (Bambu, Snapmaker, Prusa...)."""
    return sorted(glob.glob(os.path.join(HOME, "tools", "**", "resources", "profiles", "*", ""), recursive=True)
                  + glob.glob("/Applications/*.app/Contents/Resources/profiles/*/")
                  + glob.glob(os.path.join(HOME, "Applications", "*.app", "Contents", "Resources", "profiles", "*", "")))


def flatten(folder, name, kind):
    """Follow a system profile's "inherits" chain inside one vendor folder and merge it into one complete profile."""
    index = {}
    for f in glob.glob(os.path.join(folder, kind, "*.json")):
        try:
            index[json.load(open(f, encoding="utf-8-sig")).get("name")] = f   # some vendor files start with a BOM
        except (ValueError, OSError):
            pass
    chain, n = [], name
    while n:
        if n not in index:
            sys.exit(f"no {kind} profile named {n!r} in {folder}")
        d = json.load(open(index[n], encoding="utf-8-sig"))
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
        (c for c in (r"C:\Program Files\OpenSCAD\openscad.com",) + tuple(glob.glob("/Applications/OpenSCAD*.app/Contents/MacOS/OpenSCAD")) + tuple(glob.glob(os.path.join(HOME, "tools", "openscad-*", "**", "openscad.com"), recursive=True)) if os.path.exists(c)), None)
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


UNSAFE_KEYS = ("post_process",)   # a slicer setting that runs a program on the slicing computer


def safe_3mf(path, tmp):
    """A copy of a 3MF with every post-processing command taken out. A 3MF's own settings win over ours when OrcaSlicer
    loads it, so a downloaded or uploaded project could carry 'post_process' and run a program on the slicing computer
    (22 Nov 2026 review, P1). Shapes, colours and per-part settings are kept."""
    import zipfile
    dst = os.path.join(tmp, "safe-" + os.path.basename(path))
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename.lower().endswith((".config", ".json")):
                text = data.decode("utf-8", "replace")
                try:   # project settings are JSON
                    d = json.loads(text)
                    if isinstance(d, dict):
                        for k in UNSAFE_KEYS:
                            d.pop(k, None)
                        text = json.dumps(d, indent=1)
                except ValueError:   # model settings are XML: drop the metadata lines that set them
                    for k in UNSAFE_KEYS:
                        text = re.sub(r'<metadata[^>]*key="%s"[^>]*/>\s*' % k, "", text)
                data = text.encode("utf-8")
            zout.writestr(item, data)
    return dst


def made_for(path):
    """The printer a 3MF project was saved for ('' when it does not say)."""
    try:
        with zipfile.ZipFile(path) as z:
            text = z.read("Metadata/project_settings.config").decode("utf-8", "replace")
    except (KeyError, zipfile.BadZipFile, OSError):
        return ""
    m = re.search(r'"printer_model"\s*:\s*"([^"]*)"', text)
    return m.group(1) if m else ""


def remap_paint(code, states):
    """One triangle's paint_color (OrcaSlicer/Bambu TriangleSelector string) with each painted filament renumbered.
    states: {old filament number: new}; 0 (unpainted, takes the part's filament) is never changed. The hex string is
    the bit stream backwards; each node is a nibble: low 2 bits split sides (children = sides + 1), high 2 bits the
    special side, or for a leaf the state, where 0b11 means 3 + the following nibbles (a 15 reads on)."""
    nib, out, i = [int(c, 16) for c in reversed(code)], [], 0

    def node():
        nonlocal i
        c = nib[i]
        i += 1
        if c & 3:
            out.append(c)
            for _ in range((c & 3) + 1):
                node()
            return
        n = c >> 2
        if n == 3:
            while True:
                x = nib[i]
                i += 1
                n += x
                if x != 15:
                    break
        n = states.get(n, n)
        if n < 3:
            out.append(n << 2)
        else:
            out.append(0b1100)
            n -= 3
            while n >= 15:
                out.append(15)
                n -= 15
            out.append(n)

    node()
    out += nib[i:]
    return "".join("%X" % x for x in reversed(out))


def paint_states(code):
    """The filament numbers painted in one paint_color string (0, unpainted, left out)."""
    seen = {}
    remap_paint(code, _Seen(seen))
    return set(seen) - {0}


class _Seen(dict):
    """A remap table that keeps every state looked up and changes none."""
    def __init__(self, into):
        super().__init__()
        self.into = into

    def get(self, k, default=None):
        self.into[k] = True
        return default


# the model's choices only; gaps and interface layers follow our preset's layer height (24 Nov 2026: Bambu's 0.265 mm top
# gap on 0.16 mm layers gave tree supports "Potentially lost branch")
CARRY = ("enable_support", "support_type", "support_on_build_plate_only", "enable_prime_tower")


def rehome_3mf(path, tmp, heads, substitute=False, materials=None):
    """A 3MF saved for another printer (Bambu H2D, X2D, A1), sliced for this one with its colours kept: its printer
    settings are dropped (OrcaSlicer fails on them, see flatten_3mf), each of its filaments goes to the loaded head
    nearest in colour (painted triangles and whole parts both), and its support and prime-tower choices come along.
    heads: the colour on each head ('' or None when empty); materials: each head's filament (petg, pla-snapspeed): colours
    go to heads of the commonest material first, so black goes to black PETG before a nearer black in PLA. Returns (new path, carried settings, [(colour, head)])."""
    import make_3mf
    with zipfile.ZipFile(path) as z:
        project = json.loads(z.read("Metadata/project_settings.config"))
        cols = project.get("filament_colour") or ["#808080"]
        used = {1}
        for n in z.namelist():
            if n.lower().endswith(".config"):
                used |= {int(x) for x in re.findall(r'key="extruder" value="(\d+)"', z.read(n).decode("utf-8", "replace"))}
        # the material most heads hold (PETG on 3 of 4 on 24 Nov): the model's colours go there first, leaving an odd head
        # out (T3's PLA, kept for support contact). The project's own filament types are its author's printer, not ours.
        kinds = [(m or "").lower().split("-")[0] for m in materials or []]
        main = max(set(kinds), key=kinds.count) if kinds else ""
        states, missing = {}, []
        for k, c in enumerate(cols, 1):
            same = [h if h and kinds and kinds[i] == main else None for i, h in enumerate(heads)]
            mapping, miss = make_3mf.match_heads([c], same) if any(same) else ({}, [c])
            if miss:   # no close colour in that material (a PLA project on PETG heads): the nearest colour on any head
                mapping, miss = make_3mf.match_heads([c], [h or None for h in heads])
            states[k] = mapping[c.lower()] + 1
            missing += miss
        dst = os.path.join(tmp, "rehomed-" + os.path.basename(path))
        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as out:
            for item in z.infolist():
                n = item.filename
                if n in ("Metadata/project_settings.config", "Metadata/slice_info.config", "Metadata/filament_sequence.json"):
                    continue
                data = z.read(n)
                if n.lower().endswith(".model"):
                    for code in set(re.findall(rb'paint_color="([0-9A-Fa-f]+)"', data)):
                        used |= paint_states(code.decode())
                    data = re.sub(rb'paint_color="([0-9A-Fa-f]+)"',
                                  lambda m: b'paint_color="' + remap_paint(m.group(1).decode(), states).encode() + b'"', data)
                elif n == "Metadata/model_settings.config":
                    data = re.sub(r'(key="extruder" value=")(\d+)"', lambda m: m.group(1) + str(states.get(int(m.group(2)), 1)) + '"',
                                  data.decode("utf-8", "replace")).encode()
                out.writestr(item, data)
    missing = [c for k, c in enumerate(cols, 1) if c in missing and k in used]
    if missing and not substitute:
        os.remove(dst)
        sys.exit("Not loaded on any head: " + ", ".join(missing) + " (from the model's own colours). Load it, or slice "
                 "anyway with the nearest colour.")
    carried = {k: str(project[k]) for k in CARRY if k in project and not isinstance(project[k], list)}
    return dst, carried, [(c, states[k] - 1) for k, c in enumerate(cols, 1) if k in used]


def flatten_3mf(path, tmp, centre):
    """One binary STL of everything on a 3MF's build plate, laid out as its author placed it, centred on `centre` (x, y)
    and dropped to z=0. For projects saved for another printer: OrcaSlicer applies their printer settings and fails
    (22 Nov 2026: two Bambu A1 projects, one 'run found error', one segfault). Colours per part are not kept."""
    import struct
    import xml.etree.ElementTree as ET
    core, prod = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}", "{http://schemas.microsoft.com/3dmanufacturing/production/2015/06}"
    ident = [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0]

    def mat(s):
        return [float(x) for x in s.split()] if s else ident

    def mul(a, b):   # 3MF transforms are 3x4 row vectors: apply a, then b
        r = [[a[3 * i + k] for k in range(3)] for i in range(4)]
        c = [[b[3 * i + k] for k in range(3)] for i in range(4)]
        return [sum(r[i][k] * c[k][j] for k in range(3)) + (c[3][j] if i == 3 else 0) for i in range(4) for j in range(3)]

    with zipfile.ZipFile(path) as z:
        docs = {"/" + n: ET.fromstring(z.read(n)) for n in z.namelist() if n.lower().endswith(".model")}
    tris = []

    def emit(doc, oid, m, depth=0):
        if depth > 8 or doc not in docs:
            return
        o = next((x for x in docs[doc].iter(core + "object") if x.get("id") == oid), None)
        if o is None:
            return
        mesh = o.find(core + "mesh")
        if mesh is not None:
            v = []
            for e in mesh.find(core + "vertices"):
                x, y, zz = float(e.get("x")), float(e.get("y")), float(e.get("z"))
                v.append((x * m[0] + y * m[3] + zz * m[6] + m[9], x * m[1] + y * m[4] + zz * m[7] + m[10], x * m[2] + y * m[5] + zz * m[8] + m[11]))
            tris.extend((v[int(t.get("v1"))], v[int(t.get("v2"))], v[int(t.get("v3"))]) for t in mesh.find(core + "triangles"))
        comps = o.find(core + "components")
        for c in (comps if comps is not None else []):
            emit(c.get(prod + "path") or doc, c.get("objectid"), mul(mat(c.get("transform")), m), depth + 1)

    root = "/3D/3dmodel.model"
    build = docs[root].find(core + "build") if root in docs else None
    for item in (build if build is not None else []):
        emit(root, item.get("objectid"), mat(item.get("transform")))
    if not tris:
        sys.exit(f"{os.path.basename(path)}: no shapes on its build plate")
    pts = [p for t in tris for p in t]
    dx = centre[0] - (max(p[0] for p in pts) + min(p[0] for p in pts)) / 2
    dy = centre[1] - (max(p[1] for p in pts) + min(p[1] for p in pts)) / 2
    dz = -min(p[2] for p in pts)
    dst = os.path.join(tmp, os.path.splitext(os.path.basename(path))[0] + ".stl")
    with open(dst, "wb") as f:
        f.write(b"\0" * 80 + struct.pack("<I", len(tris)))
        for t in tris:
            f.write(struct.pack("<3f", 0, 0, 0) + b"".join(struct.pack("<3f", p[0] + dx, p[1] + dy, p[2] + dz) for p in t) + b"\0\0")
    return dst


def slice_(models, printer, preset="standard", material="", sets=(), out=None, timeout=900, heads=None, substitute=False):
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
        models = [safe_3mf(m, tmp) if m.lower().endswith(".3mf") else m for m in models]
        machine = json.load(open(os.path.join(folder, "machine.json"), encoding="utf-8"))
        area = [tuple(float(v) for v in xy.split("x")) for xy in machine.get("printable_area") or ["0x0", "256x256"]]
        centre = (sum(x for x, _ in area) / len(area), sum(y for _, y in area) / len(area))
        own = (machine.get("printer_model") or "").lower()
        carried, kept = {}, []
        for i, m in enumerate(models):
            if m.lower().endswith(".3mf") and made_for(m) and made_for(m).lower() != own:
                if heads:   # a tool changer that reports its colours: keep the model's colours and supports
                    models[i], c, k = rehome_3mf(m, tmp, heads, substitute, [x.strip() for x in material.split(",")] if material else None)
                    carried.update(c)
                    kept += k
                    continue
                print(f"{os.path.basename(m)} was saved for {made_for(m)}: slicing its shapes only, in one colour", file=sys.stderr)
                models[i] = flatten_3mf(m, tmp, centre)
        process = json.load(open(os.path.join(folder, "process.json"), encoding="utf-8"))
        fils = pick_materials(folder, material, max(slots_in(m) for m in models))
        fil_data = [json.load(open(f, encoding="utf-8")) for f in fils]
        if preset not in PRESETS:
            sys.exit(f"no preset {preset!r}: {', '.join(PRESETS)}")
        keys = set(machine) | set(process) | {k for d in fil_data for k in d}
        overrides = {**PRESETS[preset], **{k: v for k, v in carried.items() if k in keys | PRESET_KEYS}, **parse_sets(sets, keys | PRESET_KEYS)}
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
        # --allow-newer-file: 3MFs saved by a newer Bambu Studio (24 Nov 2026: an X2D project would not load without it)
        cmd = [orca_cli(), "--allow-newer-file", "--slice", "0", "--load-settings", f"{paths[0]};{paths[1]}", "--load-filaments", ";".join(paths[2:]),
               "--outputdir", out, "--export-3mf", f"{name}-sliced.3mf"] + models
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            sys.exit(f"OrcaSlicer ran past {timeout // 60} minutes")
    gcode = glob.glob(os.path.join(out, "*.gcode"))
    text = open(gcode[0], encoding="utf-8", errors="replace").read() if gcode else ""
    bed = re.search(r"^M190 S(\d+)", text, re.M) or re.search(r"^M140 S([1-9]\d*)", text, re.M)
    if gcode and (not bed or bed.group(1) == "0"):
        # seen 21 Nov 2026: a plate type the filament profile has no temperature for slices with the bed at 0
        plate = re.search(r"^; curr_bed_type = (.+)$", text, re.M)
        os.remove(gcode[0])
        sys.exit(f"the bed temperature came out at 0 for {plate.group(1) if plate else 'this plate'}: "
                 "set curr_bed_type to the printer's real plate (e.g. --set curr_bed_type=\"Textured PEI Plate\")")
    said = " ".join((r.stdout + r.stderr).split())
    s = stats(text) if gcode else {
        "error": "OrcaSlicer wrote nothing: " + said[-600:] + (
            " Tree supports could not reach part of the model at these settings: slice with the standard or quality preset"
            " (24 Nov 2026: a painted X2D figure failed on figure, sliced on standard)." if "lost branch" in said
            else " (open the model in the app to see why)")}
    if kept:
        s["model_colours"] = [{"colour": c, "head": f"T{h}"} for c, h in kept]
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


def new_printer(name, vendor, machine, process, filaments):
    """A printer setup from a slicer's bundled vendor profiles, e.g. Snapmaker Orca's Snapmaker folder."""
    base = next((d for d in vendor_dirs() if os.path.basename(d.rstrip("\\/")) == vendor and glob.glob(os.path.join(d, "machine", "*.json"))), None)
    if not base:
        sys.exit(f"no unpacked {vendor!r} profiles in an installed slicer (OrcaSlicer 2.5 packs them; Snapmaker Orca does not)")
    out = os.path.join(PRINTERS, name)
    os.makedirs(out, exist_ok=True)
    files = {"machine": flatten(base, machine, "machine"), "process": flatten(base, process, "process")}
    for f in filaments:
        system, _, short = f.partition(":")
        files["filament-" + (short or re.sub(r"[^a-z0-9]+", "-", system.lower()).strip("-"))] = flatten(base, system, "filament")
    for k, d in files.items():
        d["from"] = "system"
        json.dump(d, open(os.path.join(out, k + ".json"), "w", encoding="utf-8"), indent=1)
    return out, sorted(files)


def adopt_gcode(gcode_path, printer):
    """Copy the custom G-code blocks (start, end, tool change...) a known-good print was sliced with into a setup's machine.json.
    Every OrcaSlicer G-code ends with its full settings; a fork's profile can use variables upstream OrcaSlicer refuses."""
    cfg = {}
    for line in open(gcode_path, encoding="utf-8", errors="replace"):
        m = re.match(r"^; ([a-z0-9_]+) = (.*)$", line.rstrip("\n"))
        if m:
            # OrcaSlicer writes \n for a newline and \\ for a backslash: undo both in one pass
            cfg[m.group(1)] = re.sub(r"\\(.)", lambda e: {"n": "\n", "r": "\r"}.get(e.group(1), e.group(1)), m.group(2))
    if not cfg:
        sys.exit("no settings block in that G-code (slice it with OrcaSlicer or a fork)")
    path = os.path.join(printer_dir(printer), "machine.json")
    machine = json.load(open(path, encoding="utf-8-sig"))
    changed = [k for k in machine if k.endswith("_gcode") and k in cfg and isinstance(machine[k], str) and machine[k] != cfg[k]]
    for k in changed:
        machine[k] = cfg[k]
    json.dump(machine, open(path, "w", encoding="utf-8"), indent=1)
    return changed


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
    np = sub.add_parser("new-printer")
    np.add_argument("name")
    np.add_argument("--vendor", required=True, help="vendor folder name, e.g. Snapmaker")
    np.add_argument("--machine", required=True)
    np.add_argument("--process", required=True)
    np.add_argument("--filament", action="append", default=[], help='"System name[:short-name]", repeat')
    ag = sub.add_parser("adopt-gcode")
    ag.add_argument("gcode")
    ag.add_argument("--printer", required=True)
    ins = sub.add_parser("install")
    ins.add_argument("--printer", required=True)
    ins.add_argument("--user-dir", help="OrcaSlicer user preset folder (default: %%APPDATA%%/OrcaSlicer/user/default)")
    a = ap.parse_args()

    if a.cmd == "new-printer":
        out, files = new_printer(a.name, a.vendor, a.machine, a.process, a.filament)
        print(f"{out}: {', '.join(files)}")
        return
    if a.cmd == "adopt-gcode":
        changed = adopt_gcode(a.gcode, a.printer)
        print("took from the G-code:", ", ".join(changed) or "nothing (already the same)")
        return
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
