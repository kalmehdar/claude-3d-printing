"""A print that ended while Workshop was not watching still leaves the bed "part waiting"; a bed a person cleared after
that job ended stays clear.  python tools/test_bed_restart.py"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import workshop as w  # noqa: E402

w.BED = os.path.join(tempfile.mkdtemp(), "bed.json")
end = [time.time() - 60]
w._get = lambda path, timeout=15: {"jobs": [{"end_time": end[0], "filename": "a.gcode"}]}

json.dump({"state": "clear", "t": time.time() - 3600}, open(w.BED, "w"))   # cleared an hour ago, job ended a minute ago
w.bed_after_restart({"state": "complete", "file": "a.gcode"})
assert w.bed()["state"] == "part waiting", w.bed()

json.dump({"state": "clear", "t": time.time()}, open(w.BED, "w"))          # cleared after the job ended
w.bed_after_restart({"state": "cancelled", "file": "a.gcode"})
assert w.bed()["state"] == "clear"

w.bed_after_restart({"state": "printing"})                                  # a print is running: untouched
assert w.bed()["state"] == "clear"
json.dump({"state": "clear", "t": time.time() - 3600}, open(w.BED, "w"))
w.bed_after_restart({"state": "standby"})                                   # power-cycled after the end: the part is there
assert w.bed()["state"] == "part waiting"

def down(path, timeout=15):
    raise OSError("no history")
w._get = down                                                               # unknown: assume the part is there
w.bed_after_restart({"state": "error", "file": "a.gcode"})
assert w.bed()["state"] == "part waiting"
print("bed after restart check passed")
