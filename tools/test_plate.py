"""The build plate: slicing follows it, and PETG on the smooth plate is refused until glue is confirmed.  python tools/test_plate.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import workshop as w  # noqa: E402

w.PLATE = os.path.join(tempfile.mkdtemp(), "plate.json")
seen = {}
w._slice_into = lambda out, d, parts, heads, material, note, preset, sets, req, m3, orca: (seen.update(sets=list(sets)), (200, {"ok": True}))[1]
w.loaded_heads = lambda: [{"colour": "#f2f2f0", "material": "petg-white", "label": "PETG white"},
                          {"colour": "#e2dedb", "material": "pla-snapspeed", "label": "PLA white"}]
petg, pla, d = [("p", "a.stl", "#f2f2f0")], [("p", "a.stl", "#e2dedb")], {"id": "t", "name": "t"}

assert w.plate()["plate"] == "textured"   # nothing recorded: the textured plate
assert w.slice_parts(d, petg, {})[0] == 200 and "curr_bed_type=Textured PEI Plate" in seen["sets"], seen
w.plate("smooth", who="test")
code, res = w.slice_parts(d, petg, {})
assert code == 409 and "glue" in res["error"], res                 # PETG on smooth: refused without glue
assert w.slice_parts(d, petg, {"glue": True})[0] == 200 and "curr_bed_type=High Temp Plate" in seen["sets"], seen
assert w.slice_parts(d, pla, {})[0] == 200                          # PLA on smooth needs no glue
print("plate checks passed")
