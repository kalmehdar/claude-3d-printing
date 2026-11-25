"""Named size sets for an OpenSCAD design, in the file OpenSCAD's Customizer reads (model.json beside model.scad).

    python param_sets.py init model.scad [--name default]     write model.json with one set: the design's current sizes
    python param_sets.py add model.scad NAME name=value ...   add or replace a set: the design's sizes with these changed
    python param_sets.py check model.scad [...] [--render]    every set names real sizes; --render also builds each set

In OpenSCAD: Window > Customizer, pick a set from the list at the top, change sizes, Render, Export.
Sizes are the top-level assignments before the first module, the ones the Customizer shows. Standard library only.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ASSIGN = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+?)\s*;")


def sizes(scad):
    """{name: value text} for the top-level assignments the Customizer shows (before the first module or function)."""
    out = {}
    for line in open(scad, encoding="utf-8"):
        if re.match(r"\s*(module|function)\b", line):
            break
        code = line.split("//")[0]
        for part in code.split(";"):
            m = ASSIGN.match(part + ";")
            if m and not m.group(1).startswith("$"):
                out[m.group(1)] = m.group(2)
    return out


def path_for(scad):
    return os.path.splitext(scad)[0] + ".json"


def load(scad):
    try:
        return json.load(open(path_for(scad), encoding="utf-8"))
    except OSError:
        return {"parameterSets": {}, "fileFormatVersion": "1"}


def save(scad, data):
    json.dump(data, open(path_for(scad), "w", encoding="utf-8", newline="\n"), indent=2)
    print("wrote", path_for(scad))


def unquote(v):
    return v[1:-1] if len(v) >= 2 and v[0] == v[-1] == '"' else v


def openscad():
    exe = os.environ.get("OPENSCAD") or shutil.which("openscad") or next(
        (p for p in ("/Applications/OpenSCAD.app/Contents/MacOS/OpenSCAD", r"C:\Program Files\OpenSCAD\openscad.com") if os.path.exists(p)), None)
    if not exe:
        sys.exit("OpenSCAD not found: set OPENSCAD")
    return exe


def check(scad, render):
    known = sizes(scad)
    sets = load(scad).get("parameterSets", {})
    problems = []
    if not sets:
        problems.append("no sets: run `param_sets.py init`")
    for name, values in sets.items():
        for k in values:
            if k == "part":
                continue
            if k not in known:
                problems.append(f"set {name!r} names {k!r}, which the design no longer has")
        if render and not problems:
            with tempfile.TemporaryDirectory() as tmp:
                out = os.path.join(tmp, "x.stl")
                r = subprocess.run([openscad(), "-o", out, "-p", path_for(scad), "-P", name, scad], capture_output=True, text=True)
                built = r.returncode == 0 and "ERROR" not in r.stderr and os.path.exists(out) and os.path.getsize(out) > 0
                if not built:
                    problems.append(f"set {name!r} does not build: " + " ".join((r.stderr or r.stdout).split())[-300:])
    print(f"{os.path.basename(scad)}: {len(sets)} sets" + (" ok" if not problems else ""))
    for p in problems:
        print("  " + p)
    return not problems


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0], formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("init")
    i.add_argument("scad")
    i.add_argument("--name", default="default")
    a_ = sub.add_parser("add")
    a_.add_argument("scad")
    a_.add_argument("name")
    a_.add_argument("values", nargs="+", metavar="name=value")
    c = sub.add_parser("check")
    c.add_argument("scad", nargs="+")
    c.add_argument("--render", action="store_true", help="also build every set with OpenSCAD")
    a = ap.parse_args()
    if a.cmd == "init":
        data = load(a.scad)
        data["parameterSets"][a.name] = {k: unquote(v) for k, v in sizes(a.scad).items()}
        save(a.scad, data)
    elif a.cmd == "add":
        known = sizes(a.scad)
        new = {k: unquote(v) for k, v in known.items()}   # every size, so switching sets never keeps the last set's values
        for kv in a.values:
            k, _, v = kv.partition("=")
            if k not in known:
                sys.exit(f"{k!r} is not a size in {os.path.basename(a.scad)}: {', '.join(known)}")
            new[k] = unquote(v)
        data = load(a.scad)
        data["parameterSets"][a.name] = new
        save(a.scad, data)
    else:
        sys.exit(0 if all([check(s, a.render) for s in a.scad]) else 1)


if __name__ == "__main__":
    main()
