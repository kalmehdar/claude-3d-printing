"""PETG on the smooth plate: for a 3MF, only the heads it really prints with count (review, 24 Nov 2026).
python tools/test_glue_3mf.py  (no printer, no slicer: both are stand-ins)"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orca  # noqa: E402
import workshop as w  # noqa: E402

w.CFG.update(profile="u1", bed=270)
heads = [{"colour": "#f2f2f0", "material": "petg"}, {"colour": "#1c1c1c", "material": "petg"},
         {"colour": "#1e4fa0", "material": "petg"}, {"colour": "#080a0d", "material": "pla-snapspeed"}]
w.loaded_heads = lambda: heads
w.upload_gcode = lambda *a: None
for plate_on, head, glue, want in (("smooth", "T3", False, 200), ("smooth", "T1", False, 409), ("smooth", "T1", True, 200),
                                   ("textured", "T1", False, 200), ("smooth", None, False, 409)):   # None: kept its own heads, all count
    w.plate = lambda *a, p=plate_on, **k: {"plate": p}
    mc = [{"colour": "#000000", "head": head}] if head else []
    orca.slice_ = lambda models, prof, mc=mc, **kw: {"time": "1h", "grams": [1.0], "total_g": 1.0, "changes": 0, "settings": {},
                                                    "gcode": os.devnull, "model_colours": mc}
    code, out = w.slice_parts({"id": "x", "name": "x"}, [("x", "/tmp/x.3mf", "#888888")], {"upload": False, "glue": glue})
    assert code == want, (plate_on, head, glue, code, out)
print("3MF glue check passed")
