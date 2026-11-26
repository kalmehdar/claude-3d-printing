"""Checks for orca.py that need no OrcaSlicer: plain-name settings, materials, presets.  python tools/test_orca.py"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orca  # noqa: E402

keys = {"sparse_infill_density", "enable_support", "wall_loops", "layer_height"}
assert orca.parse_sets(["infill=30", "supports=on", "walls=4"], keys) == {"sparse_infill_density": "30%", "enable_support": "1", "wall_loops": "4"}
assert orca.parse_sets(["layer_height=0.16", "supports=off"], keys) == {"layer_height": "0.16", "enable_support": "0"}
assert orca.parse_sets(["brim=off", "ironing=on"], {"brim_type", "ironing_type"}) == {"brim_type": "no_brim", "ironing_type": "top"}
try:
    orca.parse_sets(["infil=5"], keys)
    raise AssertionError("a misspelt setting must be refused")
except SystemExit as e:
    assert "infill" in str(e)

d = {"wall_loops": "2", "nozzle_temperature": ["250", "250"]}
orca.apply(d, {"wall_loops": "4", "nozzle_temperature": "240", "not_here": "1"})
assert d == {"wall_loops": "4", "nozzle_temperature": ["240", "240"]}   # lists keep their length, unknown keys are not added

with tempfile.TemporaryDirectory() as t:
    for n in ("petg-black", "petg-red", "pla-white"):
        json.dump({"name": n}, open(os.path.join(t, f"filament-{n}.json"), "w"))
    pick = [os.path.basename(p) for p in orca.pick_materials(t, "red,pla", 3)]
    assert pick == ["filament-petg-red.json", "filament-pla-white.json", "filament-pla-white.json"], pick
    assert [os.path.basename(p) for p in orca.pick_materials(t, "", 1)] == ["filament-petg-black.json"]

with tempfile.TemporaryDirectory() as t:   # adopt-gcode: custom G-code from a known-good print replaces the profile's
    json.dump({"machine_start_gcode": "{if fork_only_var==0}X{endif}", "machine_end_gcode": "END", "printable_area": "0x0"},
              open(os.path.join(t, "machine.json"), "w", encoding="utf-8"))
    g = os.path.join(t, "good.gcode")
    open(g, "w").write("G1 X1\n; machine_start_gcode = PRINT_START\\nG28\n; machine_end_gcode = END\n; printable_area = 9x9\n"
                       "; change_filament_gcode = M117 a\\\\nb\n")   # an escaped backslash before n is not a newline
    json.dump({**json.load(open(os.path.join(t, "machine.json"))), "change_filament_gcode": ""}, open(os.path.join(t, "machine.json"), "w"))
    assert orca.adopt_gcode(g, t) == ["machine_start_gcode", "change_filament_gcode"]
    m = json.load(open(os.path.join(t, "machine.json")))
    assert m["machine_start_gcode"] == "PRINT_START\nG28" and m["printable_area"] == "0x0"   # only *_gcode keys move
    assert m["change_filament_gcode"] == "M117 a\\nb", repr(m["change_filament_gcode"])

import make_3mf  # noqa: E402   (tool changers: each colour goes to the head holding it)
m, missing = make_3mf.match_heads(["#1f1f22", "#ecebe6", "#002fa7"], ["#e2dedb", "#080a0d", "#e72f1d", "#f4c032"])
assert m == {"#1f1f22": 1, "#ecebe6": 0, "#002fa7": 1} and missing == ["#002fa7"], (m, missing)

p = {"wall_loops": "2"}
orca.apply(p, {"alternate_extra_wall": "1"}, add=True)
assert p == {"wall_loops": "2", "alternate_extra_wall": "1"}   # a preset can set an Orca default the profile leaves out
assert orca.parse_sets(["inner_wall_flow_ratio=1.1"], keys | orca.PRESET_KEYS) == {"inner_wall_flow_ratio": "1.1"}
assert orca.PRESETS["strong"]["alternate_extra_wall"] == "1" and orca.PRESETS["solid"]["sparse_infill_density"] == "100%"

assert all(isinstance(v, str) for p in orca.PRESETS.values() for v in p.values())
assert set(orca.PRESETS) == set(orca.PRESET_WORDS)
print("orca.py checks passed")

import slice_stats  # noqa: E402
assert slice_stats.seconds("5h 47m 45s") == 20865 and slice_stats.seconds("1d 2h") == 93600
assert slice_stats.clock(20865) == "5h 48m" and slice_stats.clock(2578) == "43m"   # plates added up, to the minute
print("slice_stats checks passed")

# look.py: a model's top-level include/use lines are lifted out of the module (BOSL2 failed inside it, 21 Nov 2026)
import look   # noqa: E402
with tempfile.TemporaryDirectory() as t:
    open(os.path.join(t, "lib.scad"), "w").write("module thing() {}\n")
    m = os.path.join(t, "m.scad")
    open(m, "w").write("include <lib.scad>\nuse <BOSL2/std.scad>\npart = \"all\";\nthing();\n")
    hoisted, rest = look.hoist_libraries(m)
    assert hoisted == f"include <{os.path.join(t, 'lib.scad')}>\nuse <BOSL2/std.scad>\n", hoisted   # local made absolute
    body = open(rest).read()
    assert "include" not in body and "use <" not in body and "thing();" in body
    os.remove(rest)
    plain = os.path.join(t, "p.scad"); open(plain, "w").write("cube(1);\n")
    assert look.hoist_libraries(plain) == ("", plain)
print("look.py checks passed")
with tempfile.TemporaryDirectory() as t:   # rotate: a 10 x 2 x 4 box stood upright lies flat after [90, 0, 0]
    import make_3mf, struct as _st
    box = os.path.join(t, "box.stl")
    v = [(x, y, z) for x in (0, 10) for y in (0, 2) for z in (0, 4)]
    faces = [(0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5), (0, 4, 5), (0, 5, 1), (2, 3, 7), (2, 7, 6), (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3)]
    with open(box, "wb") as f:
        f.write(b"\0" * 80 + _st.pack("<I", len(faces)))
        for a in faces:
            f.write(_st.pack("<3f", 0, 0, 0) + b"".join(_st.pack("<3f", *v[k]) for k in a) + b"\0\0")
    rv, _ = make_3mf.read_stl(slice_stats.rotated(box, [90, 0, 0], t))
    size = [round(max(p[a] for p in rv) - min(p[a] for p in rv), 3) for a in range(3)]
    assert size == [10, 4, 2], size
print("rotate check passed")

# a 3MF's own post_process (a command run on the slicing computer) is stripped before slicing (22 Nov 2026 review)
import io as _io, json as _json, tempfile as _tf, zipfile as _zf
with _tf.TemporaryDirectory() as _d:
    _p = os.path.join(_d, "evil.3mf")
    with _zf.ZipFile(_p, "w") as _z:
        _z.writestr("Metadata/project_settings.config", _json.dumps({"post_process": ["touch /tmp/x"], "filament_colour": ["#FFFFFF"]}))
        _z.writestr("Metadata/model_settings.config", '<config><object id="1">\n<metadata key="post_process" value="touch /tmp/x"/>\n<metadata key="name" value="a"/></object></config>')
        _z.writestr("3D/3dmodel.model", "<model/>")
    _q = orca.safe_3mf(_p, _d)
    with _zf.ZipFile(_q) as _z:
        _proj = _json.loads(_z.read("Metadata/project_settings.config")); _mod = _z.read("Metadata/model_settings.config").decode()
    assert "post_process" not in _proj and _proj["filament_colour"] == ["#FFFFFF"], _proj
    assert "post_process" not in _mod and 'key="name"' in _mod, _mod
print("3MF post_process strip check passed")

with tempfile.TemporaryDirectory() as t:   # a 3MF saved for another printer is flattened: components and transforms applied, centred, on z=0
    import struct
    import zipfile
    core = 'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06"'
    part = (f'<model {core}><resources><object id="1" type="model"><mesh><vertices><vertex x="0" y="0" z="5"/><vertex x="10" y="0" z="5"/>'
            '<vertex x="0" y="20" z="5"/></vertices><triangles><triangle v1="0" v2="1" v3="2"/></triangles></mesh></object></resources></model>')
    root = (f'<model {core}><resources><object id="2" type="model"><components><component p:path="/3D/Objects/a.model" objectid="1" '
            'transform="2 0 0 0 1 0 0 0 1 100 0 0"/></components></object></resources><build><item objectid="2" transform="1 0 0 0 1 0 0 0 1 0 50 0"/></build></model>')
    p3 = os.path.join(t, "bambu.3mf")
    with zipfile.ZipFile(p3, "w") as z:
        z.writestr("3D/3dmodel.model", root)
        z.writestr("3D/Objects/a.model", part)
        z.writestr("Metadata/project_settings.config", '{"printer_model": "Bambu Lab A1"}')
    assert orca.made_for(p3) == "Bambu Lab A1"
    b = open(orca.flatten_3mf(p3, t, (135, 135)), "rb").read()
    assert struct.unpack("<I", b[80:84])[0] == 1
    v = [struct.unpack("<3f", b[96 + 12 * i:108 + 12 * i]) for i in range(3)]
    assert [tuple(round(c, 3) for c in p) for p in v] == [(125, 125, 0), (145, 125, 0), (125, 145, 0)], v

# painted 3MF from another printer: paint states and part extruders go to the head nearest in colour (24 Nov 2026)
assert orca.remap_paint("8", {2: 1}) == "4"                       # a leaf painted filament 2 -> 1
assert orca.remap_paint("4", {1: 4}) == "1C"                      # filament 4 = 3 + 1: '11' leaf then a nibble
assert orca.remap_paint("1C", {4: 2}) == "8"
assert orca.remap_paint("0", {1: 2}) == "0"                       # unpainted stays unpainted
assert orca.remap_paint("0FC", {18: 2}) == "8"                     # 3 + 15 + 0 = filament 18 reads on, then remaps
assert orca.remap_paint("8", {2: 18}) == "0FC"
real = "08800200208002006088068AA388080900200A0800200608006085A63880"   # a split tree from a Bambu X2D model
assert orca.remap_paint(real, {}) == real                          # decode and encode round trip
assert orca.remap_paint(orca.remap_paint(real, {1: 2, 2: 1}), {1: 2, 2: 1}) == real
with tempfile.TemporaryDirectory() as t:
    import zipfile
    p3 = os.path.join(t, "h2d.3mf")
    with zipfile.ZipFile(p3, "w") as z:
        z.writestr("3D/3dmodel.model", '<model><triangle v1="0" v2="1" v3="2" paint_color="8"/><triangle paint_color="4"/></model>')
        z.writestr("Metadata/model_settings.config", '<config><object id="2"><metadata key="extruder" value="2"/></object></config>')
        z.writestr("Metadata/project_settings.config", '{"printer_model": "Bambu Lab H2D", "filament_colour": ["#000000", "#FFFFFF"], '
                   '"filament_type": ["PETG", "PETG"], '
                   '"enable_support": "1", "support_type": "tree(auto)", "enable_prime_tower": "1", "post_process": []}')
    heads = ["#f2f2f0", "#1c1c1c", "#1e4fa0", "#080a0d"]            # T0 white, T1 black, T2 blue PETG; T3 black PLA
    mats = ["petg", "petg", "petg", "pla-snapspeed"]
    dst, carried, kept = orca.rehome_3mf(p3, t, heads, materials=mats)   # black PETG goes to T1, not the nearer PLA black
    with zipfile.ZipFile(dst) as z:
        assert "Metadata/project_settings.config" not in z.namelist()
        m = z.read("3D/3dmodel.model").decode()
        assert 'paint_color="4"' in m and 'paint_color="8"' in m, m   # white (2) -> T0 = filament 1, black (1) -> T1 = 2
        assert 'value="1"' in z.read("Metadata/model_settings.config").decode()
    assert carried == {"enable_support": "1", "support_type": "tree(auto)", "enable_prime_tower": "1"}, carried
    assert kept == [("#000000", 1), ("#FFFFFF", 0)], kept
    try:
        orca.rehome_3mf(p3, t, ["#f2f2f0", "#1e4fa0", "", ""])          # no black loaded (only painted, so counted from the paint)
        raise AssertionError("missing colour not refused")
    except SystemExit as e:
        assert "#000000" in str(e.code)
    assert orca.rehome_3mf(p3, t, ["#f2f2f0", "#1e4fa0", "", ""], substitute=True)[2] == [("#000000", 1), ("#FFFFFF", 0)]
print("painted 3MF remap check passed")
