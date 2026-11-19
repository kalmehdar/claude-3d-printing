"""Studio pictures of every design in a bench.json, in its print colours: for READMEs, posts and the slicer-free
"does this look right" check.

    blender --background --factory-startup --python render.py -- bench.json out_dir [--size 1600x1000] [--only id]

One PNG per model (<id>.png): a three-quarter view from the front right, soft studio light, a pale floor with a
contact shadow. The colours are the bench's hex colours, so a render matches the Print Bench page.
"""
import json
import os
import sys

import bpy
from mathutils import Vector

args = sys.argv[sys.argv.index("--") + 1:]
src, out = args[0], os.path.abspath(args[1])
W, H = (int(x) for x in (args[args.index("--size") + 1] if "--size" in args else "1600x1000").split("x"))
only = args[args.index("--only") + 1] if "--only" in args else None
bench = json.load(open(src, encoding="utf-8"))
base = os.path.dirname(os.path.abspath(src))
os.makedirs(out, exist_ok=True)


def lin(c):   # sRGB hex to the linear values Blender's materials take
    v = [int(c.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in v] + [1.0]


def material(hexcol):
    m = bpy.data.materials.new(hexcol)
    m.use_nodes = True
    p = m.node_tree.nodes["Principled BSDF"]
    p.inputs["Base Color"].default_value = lin(hexcol)
    p.inputs["Roughness"].default_value = 0.55   # PETG: a soft sheen, not plastic-toy gloss
    return m


def area(name, loc, energy, size):
    d = bpy.data.lights.new(name, "AREA"); d.energy = energy; d.size = size
    o = bpy.data.objects.new(name, d); o.location = loc
    bpy.context.collection.objects.link(o)
    o.rotation_euler = (Vector((0, 0, 0)) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()


for m in bench["models"]:
    if only and m["id"] != only:
        continue
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.render.engine = "BLENDER_EEVEE"
    sc.render.resolution_x, sc.render.resolution_y = W, H
    sc.render.film_transparent = False
    sc.view_settings.view_transform = "AgX"
    try:
        sc.view_settings.look = "AgX - Medium High Contrast"
    except TypeError:
        pass
    sc.eevee.use_raytracing = True          # contact shadows and soft reflections
    sc.eevee.use_shadows = True
    world = bpy.data.worlds.new("w"); sc.world = world; world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = lin("#e9e8e4")
    world.node_tree.nodes["Background"].inputs[1].default_value = 0.25
    objs = []
    for label, rel, colour in m["parts"]:
        bpy.ops.wm.stl_import(filepath=os.path.join(base, rel))
        o = bpy.context.selected_objects[0]
        o.data.materials.append(material(colour))
        objs.append(o)
    lo = Vector([min((o.matrix_world @ Vector(c))[i] for o in objs for c in o.bound_box) for i in range(3)])
    hi = Vector([max((o.matrix_world @ Vector(c))[i] for o in objs for c in o.bound_box) for i in range(3)])
    centre, span = (lo + hi) / 2, hi - lo
    for o in objs:
        o.location -= Vector((centre.x, centre.y, lo.z))   # centred, standing on the floor
    r = max(span.length / 2, 10)
    bpy.ops.mesh.primitive_plane_add(size=r * 400, location=(0, 0, -0.01))
    bpy.context.object.data.materials.append(material("#e9e8e4"))
    k = r * r   # STL millimetres come in as Blender metres, so light power scales with the model's size squared
    area("key", (r * 1.4, -r * 1.8, r * 3.0), 320 * k, r * 1.2)     # small and high: a defined contact shadow
    area("fill", (-r * 2.6, -r * 1.2, r * 1.2), 90 * k, r * 3)
    area("rim", (-r * 0.6, r * 2.6, r * 2.2), 220 * k, r * 1.5)     # edge light so black parts separate from the floor
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    bpy.context.collection.objects.link(cam); sc.camera = cam
    cam.data.lens = 70
    direction = Vector((0.55, -1.35, 0.95)).normalized()
    cam.rotation_euler = (-direction).to_track_quat("-Z", "Y").to_euler()
    corners = [Vector((x, y, z)) for x in (-span.x / 2, span.x / 2) for y in (-span.y / 2, span.y / 2) for z in (0, span.z)]
    bpy.context.view_layer.update()
    co, _ = cam.camera_fit_coords(bpy.context.evaluated_depsgraph_get(), [v for c in corners for v in c])
    cam.location = co + direction * (co - Vector((0, 0, span.z / 2))).length * 0.12   # step back: a margin round the print
    sc.render.filepath = os.path.join(out, m["id"] + ".png")
    bpy.ops.render.render(write_still=True)
    print("rendered", sc.render.filepath)
