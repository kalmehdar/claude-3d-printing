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
    open(g, "w").write("G1 X1\n; machine_start_gcode = PRINT_START\\nG28\n; machine_end_gcode = END\n; printable_area = 9x9\n")
    assert orca.adopt_gcode(g, t) == ["machine_start_gcode"]
    m = json.load(open(os.path.join(t, "machine.json")))
    assert m["machine_start_gcode"] == "PRINT_START\nG28" and m["printable_area"] == "0x0"   # only *_gcode keys move

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
