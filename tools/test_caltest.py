"""One-tap calibration tests: values, the per-piece G-code, the answer by piece number and the profile write.
python tools/test_caltest.py  (no printer, no slicer: G-code and profile are made up here)"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import caltest  # noqa: E402
import workshop as w  # noqa: E402

# values: PA from 0, flow round the profile, temperature hottest first
assert caltest.values("pressure-advance", 0.04) == [0, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07]
assert caltest.values("flow-ratio", 0.95) == [0.91, 0.92, 0.93, 0.94, 0.95, 0.96, 0.97, 0.98, 0.99]
assert caltest.values("temperature", 245) == [255, 250, 245, 240, 235]
for bad in ({"start": -0.1}, {"count": 20}):
    try:
        caltest.values("pressure-advance", 0.04, **bad)
        raise AssertionError(bad)
    except ValueError:
        pass

# one object per piece, each its own build item on the chosen head
data, plan = caltest.build("flow-ratio", 1, 0.95)
assert plan["objects"] == [f"flow-{i}" for i in range(1, 10)] and len(plan["pieces"]) == 9
import io, zipfile  # noqa: E401,E402
z = zipfile.ZipFile(io.BytesIO(data))
assert z.read("3D/3dmodel.model").count(b"<item ") == 9 and z.read("Metadata/model_settings.config").count(b'key="extruder" value="2"') == 9

# objects: command after START, the profile's value back after END; a piece the slicer lost is an error
g = "\n".join(f"EXCLUDE_OBJECT_START NAME=flow-{n}_id_{n - 1}_copy_0\nG1 X1 E1\nEXCLUDE_OBJECT_END NAME=flow-{n}_id_{n - 1}_copy_0" for n in range(1, 10))
out = caltest.patch(g, plan).splitlines()
i = out.index("EXCLUDE_OBJECT_START NAME=flow-6_id_5_copy_0")
assert out[i + 1] == "M221 S101.1 ; piece 6 = flow 0.96", out[i + 1]   # 0.96 / 0.95, as hand-made on 24 Nov
assert out[out.index("EXCLUDE_OBJECT_END NAME=flow-6_id_5_copy_0") + 1] == "M221 S100"
try:
    caltest.patch(g.replace("flow-9_", "x-9_"), plan)
    raise AssertionError("lost piece not caught")
except ValueError:
    pass
_, pa = caltest.build("pressure-advance", 0, 0.04)
o = caltest.patch("EXCLUDE_OBJECT_START NAME=pa-3_id_2_copy_0\nEXCLUDE_OBJECT_END NAME=pa-3_id_2_copy_0\n" + "\n".join(
    f"EXCLUDE_OBJECT_START NAME=pa-{n}_id_0_copy_0" for n in (1, 2, 4, 5, 6, 7, 8)), pa).splitlines()
assert o[1] == "SET_PRESSURE_ADVANCE ADVANCE=0.02 ; piece 3" and o[3] == "SET_PRESSURE_ADVANCE ADVANCE=0.04"

# temperature: one M104 per band, and the slicer's own later M104 held to the band
_, tp = caltest.build("temperature", 1, 245)
g = "M109 S245 T1 ; set nozzle temperature and wait\n" + "\n".join(f";Z:{z:.2f}\nG1 E1" + ("\nM104 S245 T1 ; set nozzle temperature" if z == 0.45 else "")
                                                                     for z in [0.25 + 0.2 * k for k in range(150)])
o = caltest.patch(g, tp)
assert [l for l in o.splitlines() if "band" in l and l.startswith("M104 S2") and "of 5" in l] == [
    f"M104 S{t} ; temperature band {b} of 5" for b, t in enumerate([255, 250, 245, 240, 235], 1)]
assert "M104 S255 T1 ; set nozzle temperature (held to band 1)" in o and "M109 S245 T1" in o   # start wait untouched

# the answer: piece number -> value -> profile, old profile kept
assert caltest.answer(plan, 6) == "0.96"
t = tempfile.mkdtemp()
prof = os.path.join(t, "u1")
os.makedirs(prof)
json.dump({"name": "machine"}, open(os.path.join(prof, "machine.json"), "w"))
json.dump({"filament_flow_ratio": ["0.95"], "pressure_advance": ["0.04"], "enable_pressure_advance": ["0"]}, open(os.path.join(prof, "filament-petg.json"), "w"))
w.CFG.update(profile=prof, moonraker="http://127.0.0.1:9", camera="")
w.CAL_LOG, w.CAL_PHOTOS, w.CAL_PLANS = os.path.join(t, "cal.jsonl"), os.path.join(t, "photos"), os.path.join(t, "plans.json")
w.record = lambda *a, **k: None
import printer  # noqa: E402
printer.snap = lambda path: (_ for _ in ()).throw(OSError("no camera in tests"))
plan.update(material="petg", file="cal-flow-ratio-T1-petg.gcode", t=0)
json.dump({plan["file"]: plan}, open(w.CAL_PLANS, "w"))
row = w.calibration({"file": plan["file"], "piece": 6, "save": True}, who="Khalid")[0]
assert row["test"] == "flow-ratio" and row["value"] == "0.96" and row["saved"] == {"filament_flow_ratio": "0.96"}, row
assert json.load(open(os.path.join(prof, "filament-petg.json")))["filament_flow_ratio"] == ["0.96"]
assert any(f.endswith("-before-filament-petg.json") for f in os.listdir(w.CAL_PHOTOS))
row = w.calibration({"test": "pressure-advance", "value": "0.035", "material": "petg", "save": True}, who="Khalid")[0]
d = json.load(open(os.path.join(prof, "filament-petg.json")))
assert d["pressure_advance"] == ["0.035"] and d["enable_pressure_advance"] == ["1"], d
for bad in ({"file": "nope.gcode", "piece": 1}, {"file": plan["file"], "piece": 99}, {"test": "flow-ratio", "value": "x", "material": "petg", "save": True},
            {"test": "flow-ratio", "value": "0.9", "material": "../etc", "save": True}):
    try:
        w.calibration(bad, who="Khalid")
        raise AssertionError(bad)
    except ValueError:
        pass
print("calibration test check passed")

# a profile that lives in git (alm-01's Almehdar checkout) is not edited: the change waits for a pull request
import subprocess  # noqa: E402
subprocess.run(["git", "init", "-q", prof], check=True)
subprocess.run(["git", "-C", prof, "add", "filament-petg.json"], check=True)
before = open(os.path.join(prof, "filament-petg.json")).read()
plan.update(t=1)
json.dump({plan["file"]: plan}, open(w.CAL_PLANS, "w"))
row = w.calibration({"file": plan["file"], "piece": 7, "save": True}, who="Khalid")[0]
assert row["saved"]["pending_git"] and row["saved"]["filament_flow_ratio"] == "0.97", row
assert open(os.path.join(prof, "filament-petg.json")).read() == before
print("git-tracked profile check passed")
