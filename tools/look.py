"""Look at an OpenSCAD design before exporting it: one picture with four views and the real size.

    python look.py model.scad                     writes model-look.png: angled, top, front and side views, in colour
    python look.py model.scad --part label -D wall=2 --out look.png --size 1200

Prints the bounding box in mm from the rendered mesh, so a wrong number shows before anything is printed.
Every view is rendered by OpenSCAD itself (the model's `part = "all"` preview with its colours when it has one).
Set OPENSCAD if it is not on PATH; a development snapshot renders in seconds. Standard library only.
"""
import argparse
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile

TOOLS = os.path.dirname(os.path.abspath(__file__))
# (label, rotations applied in order, each an OpenSCAD rotate([x, y, z])): turn that face up toward a camera looking
# straight down, keeping the model's +Z pointing up the picture in the front, side and angled views
VIEWS = [("angled", [(0, 0, -35), (-55, 0, 0)]), ("top", [(0, 0, 0)]), ("front", [(-90, 0, 0)]), ("side", [(0, 0, -90), (-90, 0, 0)])]


def footprint(size, rots):
    """Width and depth seen from above after each OpenSCAD rotate([x, y, z]) in rots, in order, of a box centred on 0."""
    pts = []
    for sx in (-1, 1):
        for sy in (-1, 1):
            for sz in (-1, 1):
                x, y, z = sx * size[0] / 2, sy * size[1] / 2, sz * size[2] / 2
                for rot in rots:   # OpenSCAD's rotate([x, y, z]) turns about x, then y, then z
                    ax, ay, az = (math.radians(r) for r in rot)
                    y, z = y * math.cos(ax) - z * math.sin(ax), y * math.sin(ax) + z * math.cos(ax)
                    x, z = x * math.cos(ay) + z * math.sin(ay), -x * math.sin(ay) + z * math.cos(ay)
                    x, y = x * math.cos(az) - y * math.sin(az), x * math.sin(az) + y * math.cos(az)
                pts.append((x, y))
    return max(p[0] for p in pts) - min(p[0] for p in pts), max(p[1] for p in pts) - min(p[1] for p in pts)


def openscad():
    exe = os.environ.get("OPENSCAD") or shutil.which("openscad") or next(
        (p for p in ("/Applications/OpenSCAD.app/Contents/MacOS/OpenSCAD", r"C:\Program Files\OpenSCAD\openscad.com") if os.path.exists(p)), None)
    if not exe:
        sys.exit("OpenSCAD not found: set OPENSCAD")
    return exe


def run(args):
    r = subprocess.run([openscad()] + args, capture_output=True, text=True)
    if r.returncode:
        sys.exit("OpenSCAD failed: " + (r.stderr or r.stdout)[-800:])
    return r.stderr


def bbox(stl):
    data = open(stl, "rb").read()
    if data[:5] == b"solid" and b"facet" in data[:400]:   # ASCII
        vs = [tuple(map(float, l.split()[1:4])) for l in data.decode(errors="replace").splitlines() if l.strip().startswith("vertex")]
    else:
        n = struct.unpack_from("<I", data, 80)[0]
        vs = [struct.unpack_from("<3f", data, 84 + i * 50 + 12 + k * 12) for i in range(n) for k in range(3)]
    if not vs:
        sys.exit("the model rendered empty")
    lo = [min(v[a] for v in vs) for a in range(3)]
    hi = [max(v[a] for v in vs) for a in range(3)]
    return lo, hi


LIB_LINE = re.compile(r"^\s*(include|use)\s*<([^>]+)>\s*;?\s*$", re.M)


def hoist_libraries(scad):
    """(the model's top-level include/use lines, a sibling copy of the model without them). Relative library paths that
    exist beside the model are made absolute so they still resolve from the sheet. The copy sits beside the model so its
    own relative imports keep working; the caller's temp-dir cleanup does not reach it, so it is removed at exit."""
    import atexit
    src = open(scad, encoding="utf-8").read()
    here = os.path.dirname(os.path.abspath(scad))
    lines = []
    for m in LIB_LINE.finditer(src):
        path = os.path.join(here, m.group(2))
        lines.append(f"{m.group(1)} <{path if os.path.exists(path) else m.group(2)}>\n")
    if not lines:
        return "", scad
    copy = os.path.join(here, f".look-{os.getpid()}-{os.path.basename(scad)}")
    open(copy, "w", encoding="utf-8").write(LIB_LINE.sub("", src))
    atexit.register(lambda: os.path.exists(copy) and os.remove(copy))
    return "".join(lines), copy


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("scad")
    ap.add_argument("--part", default="all", help='which part to show (default "all", the coloured preview)')
    ap.add_argument("-D", action="append", default=[], metavar="NAME=VALUE", help="override a variable, as in OpenSCAD")
    ap.add_argument("--out")
    ap.add_argument("--size", type=int, default=1000, help="picture width in pixels")
    a = ap.parse_args()
    scad = os.path.abspath(a.scad)
    out = os.path.abspath(a.out or os.path.splitext(scad)[0] + "-look.png")
    defs = [f'-Dpart="{a.part}"'] + [f"-D{d}" for d in a.D]
    with tempfile.TemporaryDirectory() as tmp:
        stl = os.path.join(tmp, "m.stl")
        run(["-o", stl, "--export-format", "binstl"] + defs + [scad])
        lo, hi = bbox(stl)
        size = [hi[i] - lo[i] for i in range(3)]
        centre = [(hi[i] + lo[i]) / 2 for i in range(3)]
        # a 2 x 2 grid, each column as wide and each row as tall as the views in it, with a gap of 12% of the model
        fp = [footprint(size, rots) for _, rots in VIEWS]
        cells = [(0, 1), (1, 1), (0, 0), (1, 0)]
        gap = max(size) * 0.12
        colw = [max(fp[i][0] for i, (c, _) in enumerate(cells) if c == k) for k in (0, 1)]
        rowh = [max(fp[i][1] for i, (_, r) in enumerate(cells) if r == k) for k in (0, 1)]
        x_of = [gap / 2 + colw[0] / 2, gap / 2 + colw[0] + gap + colw[1] / 2]
        y_of = [gap / 2 + rowh[0] / 2, gap / 2 + rowh[0] + gap + rowh[1] / 2]
        # the model included inside a module, so it can be placed four times, each face-up for a top-down camera
        sheet = os.path.join(tmp, "sheet.scad")
        body = "".join(   # OpenSCAD applies the innermost rotate first, so the list goes outside-in reversed
            f"translate([{x_of[c]:.3f}, {y_of[r]:.3f}, 0]) "
            + "".join(f"rotate([{x}, {y}, {z}]) " for x, y, z in reversed(rots))
            + f"translate([{-centre[0]:.3f}, {-centre[1]:.3f}, {-centre[2]:.3f}]) model();\n"
            for (label, rots), (c, r) in zip(VIEWS, cells))
        # in OpenSCAD the last assignment in a scope wins, so the overrides go after the include
        overrides = "".join(f"{d.split('=', 1)[0]} = {d.split('=', 1)[1]}; " for d in [f'part="{a.part}"'] + a.D)
        # top-level include/use lines go on the sheet itself: a library's own `use` lines are not allowed inside a module
        # (BOSL2 failed with a syntax error, found on the Legion 21 Nov 2026); the rest of the model goes in the module
        hoisted, rest = hoist_libraries(scad)
        open(sheet, "w", encoding="utf-8").write(f"{hoisted}module model() {{ include <{rest}> {overrides}}}\n{body}")
        sheet_w, sheet_h = sum(colw) + 2 * gap, sum(rowh) + 2 * gap
        height = max(240, min(2 * a.size, round(a.size * sheet_h / sheet_w)))
        # orthographic: the visible height is distance x tan(fov / 2) x 2 with OpenSCAD's 22.5 degree field of view
        span = max(sheet_h, sheet_w * height / a.size) * 1.08
        dist = span / 2 / math.tan(math.radians(11.25))
        run(["-o", out, f"--imgsize={a.size},{height}", "--projection=o", "--colorscheme=Tomorrow",
             f"--camera={sheet_w / 2:.3f},{sheet_h / 2:.3f},0,0,0,0,{dist:.3f}"] + defs + [sheet])
    print(f"size: {size[0]:.1f} x {size[1]:.1f} x {size[2]:.1f} mm (x by y by z)")
    print(f"views: {out}  (top left angled, top right top, bottom left front, bottom right side)")


if __name__ == "__main__":
    main()
