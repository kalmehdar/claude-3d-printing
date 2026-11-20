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

assert all(isinstance(v, str) for p in orca.PRESETS.values() for v in p.values())
assert set(orca.PRESETS) == set(orca.PRESET_WORDS)
print("orca.py checks passed")
