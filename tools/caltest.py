"""One-tap calibration tests for a tool-changer head: pressure advance, flow ratio and temperature, each built as numbered
objects (or bands) so the person reads one number off the print. Replaces the hand-built tests of 24 Nov 2026.

  pressure-advance  8 hollow boxes, one wall, fast outer wall; SET_PRESSURE_ADVANCE per box. Pick the sharpest corners.
  flow-ratio        9 flat tiles; M221 per tile against the profile's flow. Pick the smoothest top with no gaps.
  temperature       two pillars 25 mm apart, 5 bands of 6 mm; M104 per band. Pick the band with least stringing.

build() returns the 3MF and the plan; patch() puts the per-object or per-band commands into the sliced G-code.
Never starts a print: Workshop uploads the file and the person starts it as usual."""
import io
import json
import re
import uuid
import zipfile

TESTS = {   # what each test changes, the profile key its answer belongs in, and how to read it
    "pressure-advance": {"key": "pressure_advance", "read": "the box with the sharpest corners, no bulge and no gap",
                         "sets": ["wall_loops=1", "sparse_infill_density=0%", "top_shell_layers=0", "bottom_shell_layers=2",
                                  "outer_wall_speed=120", "outer_wall_acceleration=5000"]},
    "flow-ratio": {"key": "filament_flow_ratio", "read": "the tile with the smoothest top and no gaps between lines",
                   "sets": ["top_shell_layers=3", "sparse_infill_density=15%"]},
    "temperature": {"key": "nozzle_temperature", "read": "the band with the least stringing between the pillars and clean sides",
                    "sets": ["wall_loops=2", "sparse_infill_density=15%"]},
}
BAND_MM = 6.0   # temperature band height


def values(test, current, start=None, step=None, count=None):
    """The value each numbered object (or band) gets. Defaults centre on the profile's current value."""
    current = float(current)
    if test == "pressure-advance":
        step, count = step or 0.01, count or 8
        start = 0.0 if start is None else start      # PA from 0 up: the corners say where it stops helping
    elif test == "flow-ratio":
        step, count = step or 0.01, count or 9
        start = round(current - step * (count // 2), 3) if start is None else start
    elif test == "temperature":
        step, count = step or -5, count or 5
        start = current + 10 if start is None else start   # hottest at the bottom: high to low, 5 C a band
    else:
        raise ValueError("test is " + ", ".join(TESTS))
    if not 2 <= int(count) <= 12:
        raise ValueError("count is 2 to 12")
    out = [round(start + step * i, 4) for i in range(int(count))]
    lo, hi = {"pressure-advance": (0, 0.3), "flow-ratio": (0.7, 1.3), "temperature": (170, 300)}[test]
    if not all(lo <= v <= hi for v in out):
        raise ValueError(f"{test} values stay between {lo} and {hi}: {out}")
    return out


def box(x0, y0, w, d, h):
    """An axis-aligned box: (vertices, triangles), outward facing."""
    x1, y1 = x0 + w, y0 + d
    v = [(x0, y0, 0), (x1, y0, 0), (x1, y1, 0), (x0, y1, 0), (x0, y0, h), (x1, y0, h), (x1, y1, h), (x0, y1, h)]
    t = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    return v, t


def layout(test, n, bed=270):
    """[(name, vertices, triangles)] placed on a grid round the bed centre. Names are short: the G-code calls them name_id_..."""
    c = bed / 2
    if test == "temperature":
        h = BAND_MM * n
        return [("temp-a", *box(c - 16.5, c - 4, 8, 8, h)), ("temp-b", *box(c + 8.5, c - 4, 8, 8, h))]
    w, d, h, pitch = (20, 20, 10, 26) if test == "pressure-advance" else (18, 25, 2, 22)
    cols = 4 if n > 6 else 3
    rows = -(-n // cols)
    out = []
    for i in range(n):
        r, k = divmod(i, cols)
        x = c + (k - (cols - 1) / 2) * pitch - w / 2
        y = c + ((rows - 1) / 2 - r) * (d + 6) - d / 2
        out.append((f"{'pa' if test == 'pressure-advance' else 'flow'}-{i + 1}", *box(x, y, w, d, h)))
    return out


def to_3mf(objs, head):
    """One 3MF object per test piece (so each gets its own EXCLUDE_OBJECT block), all on head (0-based)."""
    res, items, conf = [], [], []
    for i, (name, vs, ts) in enumerate(objs, start=1):
        vx = "".join(f'<vertex x="{a:.3f}" y="{b:.3f}" z="{z:.3f}"/>' for a, b, z in vs)
        tx = "".join(f'<triangle v1="{p}" v2="{q}" v3="{r}"/>' for p, q, r in ts)
        res.append(f'<object id="{i}" p:UUID="{uuid.uuid4()}" type="model"><mesh><vertices>{vx}</vertices><triangles>{tx}</triangles></mesh></object>')
        items.append(f'<item objectid="{i}" p:UUID="{uuid.uuid4()}" transform="1 0 0 0 1 0 0 0 1 0 0 0" printable="1"/>')
        conf.append(f'  <object id="{i}">\n    <metadata key="name" value="{name}"/>\n    <metadata key="extruder" value="{head + 1}"/>\n'
                    f'    <part id="{i}" subtype="normal_part"><metadata key="name" value="{name}"/></part>\n  </object>')
    model = ('<?xml version="1.0" encoding="UTF-8"?>\n<model unit="millimeter" xml:lang="en-US" '
             'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
             'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06" requiredextensions="p">\n'
             ' <metadata name="Application">Workshop calibration</metadata>\n <resources>\n  ' + "\n  ".join(res) +
             f'\n </resources>\n <build p:UUID="{uuid.uuid4()}">' + "".join(items) + '</build>\n</model>\n')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?>\n<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
                   '<Default Extension="config" ContentType="text/xml"/></Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Target="/3D/3dmodel.model" Id="rel-1" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
        z.writestr("3D/3dmodel.model", model)
        z.writestr("Metadata/model_settings.config", '<?xml version="1.0" encoding="UTF-8"?>\n<config>\n' + "\n".join(conf) + "\n</config>\n")
    return buf.getvalue()


def build(test, head, current, bed=270, **kw):
    """(3mf bytes, plan). plan: test, head, current value, [{n, value}] and how to read it; Workshop keeps it for the answer."""
    vals = values(test, current, **kw)
    objs = layout(test, len(vals), bed)
    plan = {"test": test, "head": head, "current": float(current), "key": TESTS[test]["key"], "read": TESTS[test]["read"],
            "pieces": [{"n": i + 1, "value": v} for i, v in enumerate(vals)],
            "objects": [o[0] for o in objs]}
    return to_3mf(objs, head), plan


def patch(gcode, plan):
    """The sliced G-code with each piece's setting put in. Objects: the command right after its EXCLUDE_OBJECT_START, the
    profile's own value back after its END. Temperature: an M104 at each band and every later slicer temperature line held
    to the band, so the slicer's second-layer M104 cannot undo band 1."""
    test, pieces, cur = plan["test"], plan["pieces"], plan["current"]
    out, hits = [], 0
    if test == "temperature":
        band, seen_z = None, False
        for line in gcode.splitlines():
            m = re.match(r";Z:([\d.]+)", line)
            if m:
                seen_z = True
                b = min(int(float(m.group(1)) // BAND_MM), len(pieces) - 1)
                out.append(line)
                if b != band:
                    band = b
                    hits += 1
                    out.append(f"M104 S{pieces[b]['value']:g} ; temperature band {b + 1} of {len(pieces)}")
                continue
            t = re.match(r"M104 S[\d.]+( T\d)?\s*; set nozzle temperature", line)
            if t and seen_z and band is not None:
                line = f"M104 S{pieces[band]['value']:g}{t.group(1) or ''} ; set nozzle temperature (held to band {band + 1})"
            out.append(line)
        if hits < len(pieces):
            raise ValueError(f"only {hits} of {len(pieces)} temperature bands found in the G-code")
        return "\n".join(out) + "\n"
    by_name = {f"{plan['objects'][p['n'] - 1]}": p for p in pieces}
    reset = "M221 S100" if test == "flow-ratio" else f"SET_PRESSURE_ADVANCE ADVANCE={cur:g}"
    for line in gcode.splitlines():
        out.append(line)
        m = re.match(r"EXCLUDE_OBJECT_(START|END) NAME=(.+?)_id_\d+_copy_\d+\s*$", line)
        if not m or m.group(2) not in by_name:
            continue
        p = by_name[m.group(2)]
        if m.group(1) == "END":
            out.append(reset)
            continue
        hits += 1
        if test == "pressure-advance":
            out.append(f"SET_PRESSURE_ADVANCE ADVANCE={p['value']:g} ; piece {p['n']}")
        else:   # M221 scales the profile's own flow: tile value / profile value
            out.append(f"M221 S{100 * p['value'] / cur:.1f} ; piece {p['n']} = flow {p['value']:g}")
    if hits < len(pieces):
        raise ValueError(f"only {hits} pieces found in the G-code; the slicer named the objects differently")
    return "\n".join(out) + "\n"


def answer(plan, n):
    """The value of piece n, as the profile stores it."""
    n = int(n)
    p = next((p for p in plan["pieces"] if p["n"] == n), None)
    if p is None:
        raise ValueError(f"piece is 1 to {len(plan['pieces'])}")
    return f"{p['value']:g}"


if __name__ == "__main__":
    print(json.dumps(build("flow-ratio", 1, 0.95)[1], indent=1))
