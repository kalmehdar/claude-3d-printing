"""Checks for param_sets.py that need no OpenSCAD.  python tools/test_param_sets.py"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import param_sets  # noqa: E402

with tempfile.TemporaryDirectory() as t:
    scad = os.path.join(t, "m.scad")
    open(scad, "w").write('$fn = 48;\na = 1; b = [2, 3];  // two on a line\nlabel = "HI";\n// c = 9;\nmodule m() { d = 4; }\ne = 5;\n')
    assert param_sets.sizes(scad) == {"a": "1", "b": "[2, 3]", "label": '"HI"'}, param_sets.sizes(scad)   # not $fn, comments, module or after it
    json.dump({"parameterSets": {"ok": {"a": "2", "part": "x"}, "stale": {"zz": "1"}}, "fileFormatVersion": "1"}, open(os.path.join(t, "m.json"), "w"))
    assert param_sets.check(scad, render=False) is False   # "stale" names a size the design lost
    json.dump({"parameterSets": {"ok": {"a": "2"}}, "fileFormatVersion": "1"}, open(os.path.join(t, "m.json"), "w"))
    assert param_sets.check(scad, render=False) is True
assert param_sets.unquote('"HI"') == "HI" and param_sets.unquote("4") == "4"
print("param_sets.py checks passed")
