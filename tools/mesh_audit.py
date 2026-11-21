"""Check every STL in a folder for the faults that make a slicer choke, before you print.

    blender --background --factory-startup --python mesh_audit.py -- stl/ [--bed 256]

Per file: triangles, non-manifold edges, separate pieces, area thinner than 0.8 mm (two 0.4 mm lines), overhang (area
facing down steeper than 45 degrees, above the first layer), bridge (the widest flat ceiling above the first layer: printed onto air),
inside out (negative volume) and size against the bed. Only non-manifold, inside out and too big are faults (exit code 1);
an overhang over 100 mm2 and a bridge over 15 mm are notes with the usual fix, since the slicer can support them.
Pieces: a single solid part should be 1; lettering and print sheets are many by design. Thin area: label pockets and
chamfers count too, so a big number on a solid part means look at that spot in the slicer preview.
"""
import bpy, bmesh, os, sys
from mathutils.bvhtree import BVHTree

args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else ["stl"]
folder = args[0]
BED = float(args[args.index("--bed") + 1]) if "--bed" in args else 256.0
MIN_WALL = 0.8
STEEP = -0.72        # face normal z below this faces down more than 45 degrees (a 45 degree chamfer sits at -0.707, so it passes)
FLAT = -0.99         # a flat ceiling: a bridge if nothing is under it
MAX_BRIDGE = 15.0    # mm a PETG or PLA bridge spans cleanly on a 0.4 nozzle; past it, sag
MAX_OVERHANG = 100.0 # mm2 of steep overhang worth a note; the curve under a letter or a ring top prints fine
bad = 0


def seg_dist(p, a, b):
    """Distance from p to the segment a-b, in plan (x, y)."""
    ax, ay, bx, by = a.x, a.y, b.x, b.y
    dx, dy = bx - ax, by - ay
    t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((p.x - ax) * dx + (p.y - ay) * dy) / (dx * dx + dy * dy)))
    return ((p.x - ax - t * dx) ** 2 + (p.y - ay - t * dy) ** 2) ** 0.5


print(f"{'file':<44}{'tris':>8}{'nonman':>8}{'pieces':>7}{'thin mm2':>10}{'overhang':>10}{'bridge':>8}  size / verdict")
for name in sorted(f for f in os.listdir(folder) if f.lower().endswith(".stl")):
    bpy.ops.object.select_all(action="SELECT"); bpy.ops.object.delete()
    bpy.ops.wm.stl_import(filepath=os.path.join(folder, name)); o = bpy.context.selected_objects[0]
    bm = bmesh.new(); bm.from_mesh(o.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-4)   # STL stores each triangle's corners separately
    nm = sum(1 for e in bm.edges if not e.is_manifold)
    vol = bm.calc_volume(signed=True)
    pieces, seen = 0, set()
    for f in bm.faces:
        if f.index in seen:
            continue
        pieces += 1; stack = [f]; seen.add(f.index)
        while stack:
            for e in stack.pop().edges:
                for h in e.link_faces:
                    if h.index not in seen:
                        seen.add(h.index); stack.append(h)
    thin = 0.0
    if not nm:
        bm.normal_update(); tree = BVHTree.FromBMesh(bm)
        for f in bm.faces:
            hit = tree.ray_cast(f.calc_center_median() - f.normal * 1e-4, -f.normal, MIN_WALL)
            if hit[0] is not None and hit[3] < MIN_WALL - 0.02:
                thin += f.calc_area()
    bm.normal_update()
    z0 = min(v.co.z for v in bm.verts)
    down = {f.index: f for f in bm.faces if f.calc_center_median().z - z0 > 0.3 and f.normal.z < STEEP}
    overhang = sum(f.calc_area() for f in down.values() if f.normal.z >= FLAT)
    bridge, seen = 0.0, set()   # ponytail: span = 2 x the furthest ceiling point from a supporting wall, sampled on a grid over each ceiling triangle
    for f in down.values():
        if f.normal.z >= FLAT or f.index in seen:
            continue
        patch, stack = [], [f]; seen.add(f.index)
        while stack:
            g = stack.pop(); patch.append(g)
            for e in g.edges:
                for h in e.link_faces:
                    if h.index in down and down[h.index].normal.z < FLAT and h.index not in seen:
                        seen.add(h.index); stack.append(h)
        ids = {g.index for g in patch}
        anchors = [e for g in patch for e in g.edges   # ceiling edges with a wall going down from them: what holds it up
                   if any(h.index not in ids and h.calc_center_median().z < e.verts[0].co.z - 1e-3 for h in e.link_faces)]
        if anchors:
            n = 12   # a grid of points on each ceiling triangle: the furthest point is rarely a corner or a centre
            pts = [(g.verts[0].co * (n - i - j) + g.verts[1].co * i + g.verts[2].co * j) / n
                   for g in patch if len(g.verts) == 3 for i in range(n + 1) for j in range(n + 1 - i)]
            pts = pts or [v.co for g in patch for v in g.verts]
            far = max(min(seg_dist(p, e.verts[0].co, e.verts[1].co) for e in anchors) for p in pts)
            bridge = max(bridge, 2 * far)
    d = sorted(o.dimensions)
    faults = [k for k, b in (("non-manifold", nm), ("inside out", vol < 0), ("too big for the bed", d[-1] > BED)) if b]
    note = "FIX: " + ", ".join(faults) if faults else "ok"
    tips = ([f"bridge {bridge:.0f} mm: turn it, add a rib under it, or supports"] if bridge > MAX_BRIDGE else []) +            ([f"{overhang:.0f} mm2 overhang: chamfer to 45 degrees, teardrop holes, or supports"] if overhang > MAX_OVERHANG else [])
    note += "".join("; check " + t for t in tips)
    bad += bool(faults)
    print(f"{name[-43:]:<44}{len(bm.faces):>8}{nm:>8}{pieces:>7}{thin:>10.0f}{overhang:>10.0f}{bridge:>8.1f}  {[round(x, 1) for x in o.dimensions]}  {note}")
    bm.free()
print(f"{bad} file(s) need a fix")
sys.exit(1 if bad else 0)
