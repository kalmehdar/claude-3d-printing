"""Build a Print Bench: one self-contained web page that shows every design in its print colours, with a rendered
picture of each in the list and a 3D viewer you can turn on a phone. Publish it as a Claude artifact or open it locally.

    python print_bench.py bench.json print-bench.html [--3mf 3mf]   # --3mf: a colour-set 3MF project per design

bench.json (paths relative to it):
  {"title": "Print Bench", "sub": "intro line", "foot": "footer, HTML allowed", "bed": 256,
   "models": [{"id": "card", "name": "Test card", "for": "what it is for", "status": "Ready", "state": "ready",
               "material": "PETG, four heads", "credit": "Designed by ...",
               "parts": [["Red", "stl/test-card-red.stl", "#c8102e"], ...]}]}
A part may have a fourth item, "not printed" (a circuit board shown in its case): no grams, no filament.
state is "check" (fit it first), "ready" or "later". Set "nobed": true on a model too big to sit on the bed.
Optional: "link": {"text": "Get it on GitHub", "url": "..."} puts a button in the header; "download" (a base URL, e.g. raw GitHub files) gives every part a download link, and a model's
"source" (its .scad path) a source link.
Grams are estimated from the shape (1 mm skin, 15% infill, PETG 1.27 g/cm3): about 20% either way; your slicer wins.
"""
import base64
import datetime
import json
import os
import re
import shutil
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def triangles(path):
    data = open(path, "rb").read()
    if data[:5] == b"solid" and b"facet" in data[:1000]:
        return [float(v) for m in re.findall(rb"vertex\s+(\S+)\s+(\S+)\s+(\S+)", data) for v in m]
    n = struct.unpack_from("<I", data, 80)[0]
    out = []
    for i in range(n):
        out.extend(struct.unpack_from("<9f", data, 84 + i * 50 + 12))
    return out


def grams(v, pla=False):
    vol = area = 0.0
    for i in range(0, len(v), 9):
        ax, ay, az, bx, by, bz, cx, cy, cz = v[i:i + 9]
        vol += (ax * (by * cz - bz * cy) - ay * (bx * cz - bz * cx) + az * (bx * cy - by * cx)) / 6
        ux, uy, uz, wx, wy, wz = bx - ax, by - ay, bz - az, cx - ax, cy - ay, cz - az
        area += ((uy * wz - uz * wy) ** 2 + (uz * wx - ux * wz) ** 2 + (ux * wy - uy * wx) ** 2) ** 0.5 / 2
    vol, skin = abs(vol), area * 1.0
    g = (min(vol, skin) + max(0.0, vol - skin) * 0.15) / 1000 * (1.24 if pla else 1.27)
    return round(g) if g >= 10 else round(g, 1)


def link(bench, rel):
    """A download URL for a file given relative to bench.json; '..' is resolved, which raw file hosts don't do."""
    if not bench.get("download") or not rel:
        return None
    import posixpath
    base = bench["download"].rstrip("/")
    scheme, _, rest = base.partition("://")
    host, _, path = rest.partition("/")
    return f"{scheme}://{host}/" + posixpath.normpath(posixpath.join(path, rel)).lstrip("/")


def make_project(bench, src, m, out, folder):
    """A 3MF that Bambu Studio and OrcaSlicer open with each part on its filament (tools/make_3mf.py)."""
    if not folder or m.get("nobed"):
        return None
    sys.path.insert(0, HERE)
    import make_3mf
    base = os.path.dirname(os.path.abspath(src))
    parts = [(e[0], os.path.join(base, e[1]), e[2]) for e in m["parts"] if not (len(e) > 3 and e[3] == "not printed")]
    if not parts:
        return None
    data, _ = make_3mf.build(parts, m["name"], float(bench.get("bed", 256)))
    dest = os.path.join(os.path.dirname(os.path.abspath(out)), folder)
    os.makedirs(dest, exist_ok=True)
    open(os.path.join(dest, m["id"] + ".3mf"), "wb").write(data)
    return f"{folder.strip('/')}/{m['id']}.3mf"


def board_glb(base, entry, v, shift, projects_dir, bench):
    """A board part can carry KiCad's coloured GLB (pcb.py fab): {"glb": ..., "from": the fab STL the case placed}.
    Returns its URL and the matrix that puts it where the flat board STL sits on the bench, or None."""
    extra = entry[4] if len(entry) > 4 and isinstance(entry[4], dict) else {}
    if not extra.get("glb"):
        return None
    fab = triangles(os.path.join(base, extra["from"]))
    flo = [min(fab[a::3]) for a in range(3)]
    fhi = [max(fab[a::3]) for a in range(3)]
    clo = [min(v[a::3]) for a in range(3)]
    # case.scad turns the fab STL over (rotate y 180) and moves it: x' = -x + tx, y' = y + ty, z' = -z + tz
    tx, ty, tz = clo[0] + fhi[0], clo[1] - flo[1], clo[2] + fhi[2]
    # KiCad's GLB is in metres, y up, with KiCad's y (down the board) as +z: STL x = 1000 gx, y = -1000 gz, z = 1000 gy
    cx, cy, cz = shift
    rows = [[-1000, 0, 0, tx - cx], [0, 0, -1000, ty - cy], [0, -1000, 0, tz - cz], [0, 0, 0, 1]]
    matrix = [rows[r][c] for c in range(4) for r in range(4)]   # column-major, as three.js Matrix4.fromArray wants
    src = os.path.join(base, extra["glb"])
    if projects_dir:   # published next to the page, so it loads from the same site
        os.makedirs(projects_dir, exist_ok=True)
        shutil.copyfile(src, os.path.join(projects_dir, os.path.basename(src)))
        url = os.path.basename(projects_dir.rstrip("/\\")) + "/" + os.path.basename(src)
    else:
        url = link(bench, extra["glb"])
    return {"url": url, "matrix": matrix}


def secs(t):
    """OrcaSlicer's "1d 2h 3m 4s" style to seconds."""
    return sum(int(n) * {"d": 86400, "h": 3600, "m": 60, "s": 1}[u] for n, u in re.findall(r"(\d+)\s*([dhms])", t))


def sliced_rows(stats, m):
    """Rows for the bench's "Sliced in Orca" line, one per printer in slice-stats.json (tools/slice_stats.py)."""
    names = {}
    for e in m["parts"]:
        if not (len(e) > 3 and e[3] == "not printed"):
            names.setdefault(e[2].lower(), e[0])   # filament slots follow the order colours first appear
    rows = []
    for key, p in stats.items():
        s = p.get("designs", {}).get(m["id"])
        if s and "time" in s:
            rows.append({"name": p.get("label", key), "secs": secs(s["time"]), "grams": s["grams"],
                         "colours": list(names.values()), "changes": s.get("changes", 0)})
    return rows


def main():
    args = sys.argv[1:]
    projects = None
    if "--3mf" in args:   # also write a colour-assigned 3MF project per design into this folder (next to the page)
        i = args.index("--3mf")
        projects = args[i + 1]
        del args[i:i + 2]
    src, out = args[0], args[1]
    bench = json.load(open(src, encoding="utf-8"))
    base = os.path.dirname(os.path.abspath(src))
    stats_path = os.path.join(base, "slice-stats.json")
    stats = json.load(open(stats_path, encoding="utf-8")) if os.path.exists(stats_path) else {}
    models = []
    for m in bench["models"]:
        parts, allv = [], []
        for entry in m["parts"]:
            label, rel, colour = entry[:3]
            printed = not (len(entry) > 3 and entry[3] == "not printed")   # e.g. a circuit board shown in its case
            v = triangles(os.path.join(base, rel))
            parts.append((label, rel, colour, v, grams(v, "PLA" in m.get("material", "") and "PETG" not in m.get("material", "")) if printed and not m.get("nobed") else None, entry))
            allv.extend(v)
        lo = [min(allv[a::3]) for a in range(3)]
        hi = [max(allv[a::3]) for a in range(3)]
        cx, cy = (lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2
        packed = []
        for label, rel, colour, v, g, entry in parts:
            shifted = [v[i] - (cx, cy, lo[2])[i % 3] for i in range(len(v))]
            q0 = min(shifted) if shifted else 0.0          # 16-bit vertices: a quarter of the page size of floats
            step = max((max(shifted) - q0) / 65535, 1e-6) if shifted else 1.0
            ints = [round((x - q0) / step) for x in shifted]
            url = link(bench, rel)
            glb = board_glb(base, entry, v, (cx, cy, lo[2]), projects and os.path.join(os.path.dirname(os.path.abspath(out)), projects), bench)
            packed.append({"label": label, "colour": colour, "grams": g, "url": url, "q0": q0, "qs": step, "glb": glb,
                           "data": base64.b64encode(struct.pack(f"<{len(ints)}H", *ints)).decode()})
        models.append({"id": m["id"], "name": m["name"], "for": m.get("for", ""), "status": m.get("status", ""),
                       "state": m.get("state", "ready"), "material": m.get("material", ""), "credit": m.get("credit", ""),
                       "nobed": m.get("nobed", False), "size": [round(hi[a] - lo[a], 1) for a in range(3)],
                       "parts": packed, "sliced": sliced_rows(stats, m),
                       "source": link(bench, m.get("source")),
                       "project": make_project(bench, src, m, out, projects)})
        print(f"{m['name']}: {models[-1]['size']} mm, {len(allv) // 9} triangles")
    config = {k: bench[k] for k in ("title", "sub", "foot", "mark", "markLang", "bed", "link") if k in bench}
    tpl = open(os.path.join(HERE, "print-bench-template.html"), encoding="utf-8").read()
    html = (tpl.replace("/*MODELS*/[]", json.dumps(models, separators=(",", ":")))
            .replace("/*CONFIG*/{}", json.dumps(config, ensure_ascii=False))
            .replace("/*TITLE*/", bench.get("title", "Print Bench"))
            .replace("/*BUILT*/", datetime.datetime.now().strftime("%d %b %Y")))
    # The template is a page body (Claude artifacts wrap it themselves); a standalone file needs its own document,
    # above all the viewport tag, or phones lay it out at desktop width.
    html = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
            '</head>\n<body>\n' + html.replace('<meta charset="utf-8">\n', '', 1) + '\n</body>\n</html>\n')
    open(out, "w", encoding="utf-8", newline="\n").write(html)
    print(f"wrote {out} ({os.path.getsize(out) // 1024} KB)")


if __name__ == "__main__":
    main()
