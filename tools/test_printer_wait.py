"""printer_wait returns on a state change, on reaching a layer, or when time runs out.  python tools/test_printer_wait.py
No printer: the status replies are scripted."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import printer_mcp as m  # noqa: E402

m.t_status = lambda _: "STATUS"
m.WAIT_EVERY = 0


def script(*steps):
    it = iter(steps)
    m.mr = lambda method, path, **kw: {"status": {"print_stats": next(it)}}


ps = lambda state, layer=0, msg="": {"state": state, "info": {"current_layer": layer}, "message": msg}   # noqa: E731
script(ps("printing", 3), ps("printing", 3), ps("printing", 4), ps("paused", 4, "detected noodle"))
r = m.t_wait({})
assert r.startswith("State changed: printing -> paused (detected noodle)") and r.endswith("STATUS"), r
script(ps("printing", 1), ps("printing", 2), ps("printing", 9))
assert m.t_wait({"layer": 8}).startswith("Reached layer 9."), "layer"
clock = iter([0, 0, 100, 400])
m.time.time = lambda: next(clock)
script(*[ps("printing", 5)] * 4)
assert m.t_wait({"minutes": 5}).startswith("Nothing changed in 5 min (still printing, layer 5)")
for bad in ({"minutes": 30}, {"minutes": -1}):
    try:
        m.t_wait(bad)
        raise AssertionError(bad)
    except ValueError:
        pass
print("printer_wait check passed")
