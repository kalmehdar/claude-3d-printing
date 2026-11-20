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

OrcaSlicer: set ORCA_CLI to orca-slicer.exe (upstream OrcaSlicer 2.5 or later), or it is looked for in ~/tools.
Profiles for bambu-p1s: ORCA_PROFILES, or any OrcaSlicer install found under ~/tools.
Standard library only.
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")


def find(env, patterns):
    if os.environ.get(env):
        return os.environ[env]
    for p in patterns:
        hits = sorted(glob.glob(os.path.join(HOME, "tools", p), recursive=True), reverse=True)
        if hits:
            return hits[0]
    sys.exit(f"set {env}: nothing found for {patterns}")


def flatten(folder, name, kind):
    """Follow an OrcaSlicer system profile's "inherits" chain and merge it into one complete profile."""
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
    base = os.environ.get("ORCA_PROFILES") or os.path.dirname(find("ORCA_PROFILES_BBL", ["**/resources/profiles/BBL/machine"]))
    mat = "PETG" if "PETG" in material.upper() and "PLA" not in material.upper().split("PETG")[0] else "PLA"
    files = {"machine": flatten(base, "Bambu Lab P1S 0.4 nozzle", "machine"),
             "process": flatten(base, "0.20mm Standard @BBL X1C", "process"),
             "filament": flatten(base, f"Generic {mat}", "filament")}
    files["process"]["curr_bed_type"] = "Textured PEI Plate"   # the default Cool Plate refuses PETG
    paths = {}
    for k, d in files.items():
        d["from"] = "system" if k != "filament" else d.get("from", "system")
        paths[k] = os.path.join(tmp, k + ".json")
        json.dump(d, open(paths[k], "w", encoding="utf-8"))
    return paths["machine"], paths["process"], paths["filament"]


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
    head = open(gcode, encoding="utf-8", errors="replace").read()
    t = re.search(r"; estimated printing time \(normal mode\) = (.+)", head) or re.search(r"total estimated time: ([^;\n]+)", head)   # Bambu headers use the second
    g = re.search(r"; filament used \[g\] = (.+)", head)
    c = re.search(r"; total filament change = (\d+)", head)
    if not (t and g):
        return {"error": "no time or grams in the G-code"}
    grams = [round(float(x), 1) for x in g.group(1).split(",")]
    return {"time": t.group(1).strip(), "grams": grams, "total_g": round(sum(grams), 1), "changes": int(c.group(1)) if c else 0}


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
    orca = find("ORCA_CLI", ["orcaslicer-*/orca-slicer.exe", "OrcaSlicer*/orca-slicer.exe"])
    sys.path.insert(0, HERE)
    import make_3mf

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
        with tempfile.TemporaryDirectory() as tmp:
            machine, process, filament = bambu(m.get("material", ""), tmp) if a.printer == "bambu-p1s" else custom(a.profiles, m.get("material", ""))
            data, slots = make_3mf.build(parts, m["name"], bed_of(machine))
            p3 = os.path.join(tmp, m["id"] + ".3mf")
            open(p3, "wb").write(data)
            n = max(1, len(slots) if isinstance(slots, (list, tuple)) else int(slots or 1))
            cmd = [orca, "--slice", "0", "--load-settings", f"{machine};{process}",
                   "--load-filaments", ";".join([filament] * n), "--outputdir", tmp, p3]
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
                g = glob.glob(os.path.join(tmp, "*.gcode"))
                s = stats(g[0]) if g else {"error": f"slicer exit {r.returncode}: {(r.stdout + r.stderr).strip()[-200:]}"}
            except subprocess.TimeoutExpired:
                s = {"error": "slicer ran past 15 minutes"}
        entry["designs"][m["id"]] = s
        print(f"{m['id']:<16} {s.get('time', '-'):>12}  {s.get('total_g', '-')} g  {s.get('error', '')}")
    json.dump(allstats, open(out_path, "w", encoding="utf-8", newline="\n"), indent=1)
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
