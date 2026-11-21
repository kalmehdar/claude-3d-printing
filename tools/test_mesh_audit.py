"""mesh_audit.py's overhang and bridge columns on five shapes with known answers. Needs OpenSCAD and Blender (skips otherwise).
    python tools/test_mesh_audit.py"""
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SHAPES = {   # name: (OpenSCAD, overhang mm2, bridge mm)
    "cube": ("cube(20);", 0, 0),
    "deck": ("cube([10,10,10]); translate([40,0,0]) cube([10,10,10]); translate([0,0,10]) cube([50,10,3]);", 0, 30),
    "roof": ("difference(){ cube([50,30,20]); translate([10,10,-1]) cube([30,10,11]); }", 0, 10),
    "chamfer": ("rotate([90,0,0]) linear_extrude(20) polygon([[0,0],[20,0],[20,10],[10,20],[0,20]]);", 0, 0),
    "floating": ("cube(20); translate([30,0,9]) cube(5);", 0, 0),
    "lamp-body": ("cube(20);", 0, 0),                                # two colour files of one design:
    "lamp-lens": ("translate([30,0,9]) cube(5);", 0, 0),             # the lens hangs in air beside the body
    "tag-base": ("cube([20,20,2]);", 0, 0),
    "tag-inlay": ("translate([5,5,2]) cube([10,10,0.6]);", 0, 0),    # an inlay resting on the base: fine
    "slope": ("rotate([90,0,0]) linear_extrude(20) polygon([[0,0],[10,0],[10+20*tan(60),20],[0,20]]);", 800, 0),
}
scad = os.environ.get("OPENSCAD") or shutil.which("openscad") or next(
    (p for p in (r"C:\Program Files\OpenSCAD\openscad.com", "/Applications/OpenSCAD.app/Contents/MacOS/OpenSCAD") if os.path.exists(p)), None)
blender = os.environ.get("BLENDER") or shutil.which("blender") or next(
    (p for p in sorted(glob.glob(r"C:\Program Files\Blender Foundation\*\blender.exe")) + ["/Applications/Blender.app/Contents/MacOS/Blender"]
     if os.path.exists(p)), None)
if not (scad and blender):
    print("skipped: needs OpenSCAD and Blender (set OPENSCAD / BLENDER)")
    sys.exit(0)
with tempfile.TemporaryDirectory() as t:
    for name, (code, _, _) in SHAPES.items():
        src = os.path.join(t, name + ".scad")
        open(src, "w").write(code)
        subprocess.run([scad, "-o", os.path.join(t, name + ".stl"), src], capture_output=True, check=True)
        os.remove(src)
    out = subprocess.run([blender, "--background", "--factory-startup", "--python", os.path.join(HERE, "mesh_audit.py"), "--", t],
                         capture_output=True, text=True).stdout
for name, (_, over, bridge) in SHAPES.items():
    if "-" in name or name == "floating":
        continue
    m = re.search(rf"^{name}\.stl\s+\d+\s+\d+\s+\d+\s+\d+\s+(\d+)\s+([\d.]+)", out, re.M)
    assert m, f"{name} missing from the audit:\n{out[-800:]}"
    got = (int(m.group(1)), float(m.group(2)))
    assert abs(got[0] - over) <= 1 and abs(got[1] - bridge) <= 0.2, (name, got, (over, bridge))
assert re.search(r"^check floating\.stl: 1 piece\(s\) start 9\.0 mm", out, re.M), "floating piece not reported"
assert re.search(r"^check lamp-lens\.stl: 1 piece\(s\) start 9\.0 mm", out, re.M), "floating colour part not reported"
assert not re.search(r"^check (cube|deck|roof|chamfer|slope|lamp-body|tag-base|tag-inlay)\.stl", out, re.M), "a supported piece was reported floating"
print("mesh_audit.py overhang, bridge and floating checks passed")
