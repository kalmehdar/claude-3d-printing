"""Real slicer numbers for every Print Bench design: print time and grams per filament, from OrcaSlicer itself.

    python slice_stats.py bench.json --printer bambu-p1s
    python slice_stats.py bench.json --printer u1 --profiles <folder with machine.json, process.json, filament-*.json>

Each design is packed into its colour 3MF (make_3mf.py), sliced by the OrcaSlicer command line, and the G-code
header gives the numbers. They go into slice-stats.json beside bench.json, under the printer's name, and the bench
shows them with a printer switch. Only the numbers are written: no slicer profile is copied into the repo.

  bambu-p1s   OrcaSlicer's own Bambu Lab P1S 0.4 profile, 0.20 mm Standard, Generic PLA or Generic PETG (from the
              design's "material"), read from an installed OrcaSlicer's resources/profiles/BBL folder.
  any other   --profiles: a folder holding machine.json, process.json and one filament-<anything>.json per material
              (filament-pla.json, filament-petg.json); the folder's machine.json sets the bed.

A design may carry two optional fields:
  "slice": {"prime_tower_width": "20", ...}   OrcaSlicer settings for this design only (a wide part that leaves no room
                                               for the default prime tower, say)
  "plates": "each"                             every part on its own plate (separate prints that don't fit the bed
                                               together); the numbers are added up
  "plates": [[0, 1], [2], [3]]                 the same, with parts grouped by their place in "parts" (a body and its
                                               labels stay on one plate)
  "rotate": [90, 0, 0]                         print orientation in degrees about x, then y, then z (OpenSCAD's order),
                                               for parts drawn as they sit in an assembly: every part turns together,
                                               so labels stay in their body; or one rotation per part,
                                               [[90, 0, 0], [0, 0, 0]], in the order of "parts"

OrcaSlicer: set ORCA_CLI to orca-slicer.exe (upstream OrcaSlicer 2.5 or later), or it is looked for in ~/tools.
Profiles for bambu-p1s: ORCA_PROFILES, or any OrcaSlicer install found under ~/tools.
Standard library only.
"""
import argparse
import glob
import json
import math
import os
import re
import struct
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orca   # noqa: E402  (the one place OrcaSlicer is found and its profiles read)



def bambu(material, tmp):
    folder = orca.bambu(material, tmp)
    return custom(folder, material)


def custom(folder, material):
    mat = "petg" if "PETG" in material.upper() else "pla"
    fil = sorted(glob.glob(os.path.join(folder, f"filament-*{mat}*.json"))) or sorted(glob.glob(os.path.join(folder, "filament-*.json")))
    if not fil:
        sys.exit(f"no filament-*.json in {folder}")
    return os.path.join(folder, "machine.json"), os.path.join(folder, "process.json"), fil[0]


def bed_of(machine):
    xy = [tuple(map(float, p.split("x"))) for p in json.load(open(machine, encoding="utf-8"))["printable_area"]]
    return min(max(x for x, _ in xy) - min(x for x, _ in xy), max(y for _, y in xy) - min(y for _, y in xy))


def stats(gcode):
    return orca.stats(open(gcode, encoding="utf-8", errors="replace").read())


def seconds(t):
    return sum(int(n) * {"d": 86400, "h": 3600, "m": 60, "s": 1}[u] for n, u in re.findall(r"(\d+)([dhms])", t))


def clock(sec):
    h, m = divmod(round(sec / 60), 60)
    return f"{h}h {m}m" if h else f"{m}m"


def rotated(path, deg, tmp):
    """The part turned by deg (x, then y, then z, as OpenSCAD's rotate) and written as a binary STL in tmp."""
    import make_3mf
    verts, tris = make_3mf.read_stl(path)
    for axis, d in enumerate(deg):
        c, s_ = math.cos(math.radians(d)), math.sin(math.radians(d))
        i, j = [(1, 2), (2, 0), (0, 1)][axis]
        verts = [tuple(v[i] * c - v[j] * s_ if k == i else v[i] * s_ + v[j] * c if k == j else v[k] for k in range(3)) for v in verts]
    out = os.path.join(tmp, f"r{len(os.listdir(tmp))}-" + os.path.basename(path))
    with open(out, "wb") as f:
        f.write(b"\0" * 80 + struct.pack("<I", len(tris)))
        for t in tris:
            f.write(struct.pack("<3f", 0, 0, 0) + b"".join(struct.pack("<3f", *verts[k]) for k in t) + b"\0\0")
    return out


def slice_one(exe, parts, name, machine, process, filament, overrides, tmp, rotate=None):
    """One plate: pack the parts into a colour 3MF, slice it, read the numbers from the G-code."""
    import make_3mf
    if rotate:
        turns = rotate if isinstance(rotate[0], list) else [rotate] * len(parts)
        parts = [(label, rotated(path, r, tmp) if any(r) else path, colour) for (label, path, colour), r in zip(parts, turns)]
    if overrides:
        d = json.load(open(process, encoding="utf-8"))
        orca.apply(d, overrides, add=True)
        process = os.path.join(tmp, "process.json")
        json.dump(d, open(process, "w", encoding="utf-8"))
    data, slots = make_3mf.build(parts, name, bed_of(machine))
    p3 = os.path.join(tmp, "plate.3mf")
    open(p3, "wb").write(data)
    n = max(1, len(slots) if isinstance(slots, (list, tuple)) else int(slots or 1))
    cmd = [exe, "--slice", "0", "--load-settings", f"{machine};{process}",
           "--load-filaments", ";".join([filament] * n), "--outputdir", tmp, p3]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        return {"error": "slicer ran past 15 minutes"}
    g = glob.glob(os.path.join(tmp, "*.gcode"))
    return stats(g[0]) if g else {"error": f"slicer exit {r.returncode}: {(r.stdout + r.stderr).strip()[-200:]}"}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("bench")
    ap.add_argument("--printer", required=True, help="bambu-p1s, or a name for --profiles")
    ap.add_argument("--profiles", help="folder with machine.json, process.json, filament-*.json")
    ap.add_argument("--label", help="what the bench calls this printer (default: Bambu Lab P1S, or the name)")
    ap.add_argument("--only", help="one design id")
    a = ap.parse_args()
    if a.printer != "bambu-p1s" and not a.profiles:
        sys.exit("--profiles is needed for any printer but bambu-p1s")
    exe = orca.orca_cli()

    bench = json.load(open(a.bench, encoding="utf-8"))
    base = os.path.dirname(os.path.abspath(a.bench))
    out_path = os.path.join(base, "slice-stats.json")
    allstats = json.load(open(out_path, encoding="utf-8")) if os.path.exists(out_path) else {}
    entry = allstats.setdefault(a.printer, {"label": a.label or ("Bambu Lab P1S" if a.printer == "bambu-p1s" else a.printer), "designs": {}})
    if a.label:
        entry["label"] = a.label
    for m in bench["models"]:
        if m.get("nobed") or (a.only and m["id"] != a.only):
            continue
        parts = [(e[0], os.path.join(base, e[1]), e[2]) for e in m["parts"] if not (len(e) > 3 and e[3] == "not printed")]
        if not parts:
            continue
        pl = m.get("plates")
        plates = [[p] for p in parts] if pl == "each" else [[parts[i] for i in g] for g in pl] if pl else [parts]
        results = []
        for plate in plates:
            with tempfile.TemporaryDirectory() as tmp:
                machine, process, filament = bambu(m.get("material", ""), tmp) if a.printer == "bambu-p1s" else custom(a.profiles, m.get("material", ""))
                rot = m.get("rotate")
                if rot and isinstance(rot[0], list):
                    rot = [rot[parts.index(p)] for p in plate]
                results.append(slice_one(exe, plate, m["name"], machine, process, filament, m.get("slice"), tmp, rot))
        bad = [r for r in results if "error" in r]
        if bad:
            s = bad[0]
        elif len(results) == 1:
            s = results[0]
        else:   # separate plates: time and grams add up; grams per filament slot
            grams = [round(sum(r["grams"][i] for r in results if i < len(r["grams"])), 1) for i in range(max(len(r["grams"]) for r in results))]
            s = {"time": clock(sum(seconds(r["time"]) for r in results)), "grams": grams, "total_g": round(sum(grams), 1),
                 "changes": sum(r.get("changes", 0) for r in results), "plates": len(results)}
        entry["designs"][m["id"]] = s
        print(f"{m['id']:<16} {s.get('time', '-'):>12}  {s.get('total_g', '-')} g  {s.get('error', '')}")
    json.dump(allstats, open(out_path, "w", encoding="utf-8", newline="\n"), indent=1)
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
