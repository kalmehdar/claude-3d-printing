"""One multi-colour 3MF project that Bambu Studio and OrcaSlicer open with every part already on its filament.

    python make_3mf.py bench.json <model id> out.3mf [--bed 256]
    python make_3mf.py --part body.stl:#1f1f22 --part label.stl:#ecebe6 out.3mf [--name Bin] [--bed 256]

The parts become one object (so they stay aligned), each part gets a filament slot, and each slot gets its
colour: one slot per distinct colour, in the order they first appear. The object is centred on the bed.
Parts marked "not printed" in bench.json (a circuit board shown in its case) are left out.
Print settings are not included: pick the printer, process and filament types in the slicer as usual.
Standard library only.
"""
import argparse
import json
import os
import re
import struct
import sys
import uuid
import zipfile
from xml.sax.saxutils import escape


def read_stl(path):
    data = open(path, "rb").read()
    if data[:5] == b"solid" and b"facet" in data[:1000]:
        pts = [tuple(map(float, m)) for m in re.findall(rb"vertex\s+(\S+)\s+(\S+)\s+(\S+)", data)]
    else:
        n = struct.unpack_from("<I", data, 80)[0]
        pts = [struct.unpack_from("<3f", data, 84 + i * 50 + 12 + k * 12) for i in range(n) for k in range(3)]
    verts, index, tris = [], {}, []
    for i in range(0, len(pts) - 2, 3):   # shared vertices: smaller file, and slicers see a closed mesh
        tri = []
        for p in pts[i:i + 3]:
            key = (round(p[0], 5), round(p[1], 5), round(p[2], 5))
            if key not in index:
                index[key] = len(verts)
                verts.append(key)
            tri.append(index[key])
        if len(set(tri)) == 3:
            tris.append(tri)
    return verts, tris


def rgb(h):
    h = h.lstrip("#")[:6]
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def match_heads(colours, heads, limit=110):
    """Each design colour -> the head (0-based) whose loaded filament is nearest in colour, and the colours with no close match.
    limit is a plain RGB distance: black against charcoal is about 40, red against orange about 110."""
    mapping, missing = {}, []
    loaded = [(i, h) for i, h in enumerate(heads) if h]   # None marks an empty head
    if not loaded:
        return {c.lower(): 0 for c in colours}, list(colours)
    for c in colours:
        d, i = min((sum((x - y) ** 2 for x, y in zip(rgb(c), rgb(h))) ** 0.5, i) for i, h in loaded)
        mapping[c.lower()] = i
        if d > limit:
            missing.append(c)
    return mapping, missing


def build(parts, name, bed, heads=None):
    """parts: [(label, stl path, colour hex)] -> (bytes of a .3mf, slot colours).
    heads: the colours loaded on each head of a tool changer, in head order; each part then goes to the head
    holding its colour (see match_heads) and the slots are the heads. Without heads: one slot per distinct colour."""
    colours = list(dict.fromkeys(c.lower() for _, _, c in parts))
    if heads:
        mapping, _ = match_heads(colours, heads)
        meshes = [(label, read_stl(path), mapping[c.lower()] + 1) for label, path, c in parts]
        colours = [h.lower() if h else "#808080" for h in heads]
    else:
        meshes = [(label, read_stl(path), colours.index(c.lower()) + 1) for label, path, c in parts]
    allv = [v for _, (vs, _), _ in meshes for v in vs]
    lo = [min(v[a] for v in allv) for a in range(3)]
    hi = [max(v[a] for v in allv) for a in range(3)]
    if max(hi[0] - lo[0], hi[1] - lo[1]) > bed:
        print(f"warning: {hi[0] - lo[0]:.1f} x {hi[1] - lo[1]:.1f} mm is larger than a {bed} mm bed", file=sys.stderr)
    shift = (-(lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -lo[2])   # centre in x and y, sit on z = 0
    objects, comps, settings_parts = [], [], []
    for i, (label, (vs, ts), slot) in enumerate(meshes, start=1):
        vx = "".join(f'<vertex x="{v[0] + shift[0]:.5f}" y="{v[1] + shift[1]:.5f}" z="{v[2] + shift[2]:.5f}"/>' for v in vs)
        tx = "".join(f'<triangle v1="{t[0]}" v2="{t[1]}" v3="{t[2]}"/>' for t in ts)
        objects.append(f'<object id="{i}" p:UUID="{uuid.uuid4()}" type="model"><mesh><vertices>{vx}</vertices>'
                       f'<triangles>{tx}</triangles></mesh></object>')
        comps.append(f'<component objectid="{i}" p:UUID="{uuid.uuid4()}" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>')
        settings_parts.append(f'    <part id="{i}" subtype="normal_part">\n      <metadata key="name" value="{escape(label, {chr(34): "&quot;"})}"/>\n'
                              f'      <metadata key="extruder" value="{slot}"/>\n    </part>')
    top = len(meshes) + 1
    model = ('<?xml version="1.0" encoding="UTF-8"?>\n'
             '<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
             'xmlns:BambuStudio="http://schemas.bambulab.com/package/2021" '
             'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06" requiredextensions="p">\n'
             ' <metadata name="Application">print-bench make_3mf</metadata>\n'
             ' <metadata name="BambuStudio:3mfVersion">1</metadata>\n'
             f' <metadata name="Title">{escape(name)}</metadata>\n'
             ' <resources>\n  ' + "\n  ".join(objects) + "\n"
             f'  <object id="{top}" p:UUID="{uuid.uuid4()}" type="model"><components>{"".join(comps)}</components></object>\n'
             ' </resources>\n'
             f' <build p:UUID="{uuid.uuid4()}"><item objectid="{top}" p:UUID="{uuid.uuid4()}" '
             f'transform="1 0 0 0 1 0 0 0 1 {bed / 2:.3f} {bed / 2:.3f} 0" printable="1"/></build>\n</model>\n')
    settings = ('<?xml version="1.0" encoding="UTF-8"?>\n<config>\n'
                f'  <object id="{top}">\n    <metadata key="name" value="{escape(name, {chr(34): "&quot;"})}"/>\n'
                '    <metadata key="extruder" value="1"/>\n' + "\n".join(settings_parts) + "\n  </object>\n</config>\n")
    project = json.dumps({"filament_colour": [c.upper() for c in colours]}, indent=1)
    content_types = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
                     '<Default Extension="config" ContentType="text/xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Target="/3D/3dmodel.model" Id="rel-1" '
            'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("3D/3dmodel.model", model)
        z.writestr("Metadata/model_settings.config", settings)
        z.writestr("Metadata/project_settings.config", project)
    return buf.getvalue(), colours


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("args", nargs="+", help="bench.json model-id out.3mf, or out.3mf with --part")
    ap.add_argument("--part", action="append", default=[], help="file.stl:#rrggbb (repeat, in head order)")
    ap.add_argument("--name", default=None)
    ap.add_argument("--bed", type=float, default=256.0)
    ap.add_argument("--heads", help="tool changer: the colour loaded on each head, in order, e.g. #e2dedb,#080a0d,#e72f1d,#f4c032 "
                                    "(printer.py status lists them); each part goes to the head holding its colour")
    a = ap.parse_args()
    if a.part:
        out = a.args[0]
        parts = []
        for spec in a.part:
            path, _, colour = spec.rpartition(":")
            parts.append((os.path.splitext(os.path.basename(path))[0], path, colour))
        name = a.name or os.path.splitext(os.path.basename(out))[0]
    else:
        bench_path, mid, out = a.args
        bench = json.load(open(bench_path, encoding="utf-8"))
        m = next((m for m in bench["models"] if m["id"] == mid), None)
        if not m:
            sys.exit(f"no model '{mid}' in {bench_path}")
        base = os.path.dirname(os.path.abspath(bench_path))
        parts = [(e[0], os.path.join(base, e[1]), e[2]) for e in m["parts"] if not (len(e) > 3 and e[3] == "not printed")]
        name = a.name or m["name"]
    heads = [h.strip() for h in a.heads.split(",")] if a.heads else None
    if heads:
        _, missing = match_heads([c for _, _, c in parts], heads)
        if missing:
            print("warning: not loaded on any head, printing in the nearest colour: " + ", ".join(missing), file=sys.stderr)
    data, colours = build(parts, name, a.bed, heads=heads)
    open(out, "wb").write(data)
    print(f"wrote {out}: {len(parts)} parts on {len(colours)} filament slots: " +
          ", ".join(f"{i + 1} {c}" for i, c in enumerate(colours)))


if __name__ == "__main__":
    main()
