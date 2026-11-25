"""Workshop's print-register outbox over its API: rows listed, exactly the committed ones taken out, a new row kept.
python tools/test_outbox.py  (a temporary file; no printer)"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import workshop as w  # noqa: E402

t = tempfile.mkdtemp()
f = os.path.join(t, "prints.md")
open(f, "w").write("# Print log\n\n| Date | Model (file) | For |\n| --- | --- | --- |\n| 2026-11-24 | a.gcode | ? |\n| 2026-11-24 | b.gcode | ? |\n\nfooter\n")
w.CFG["print_log"] = f
assert w.outbox()["rows"] == ["| 2026-11-24 | a.gcode | ? |", "| 2026-11-24 | b.gcode | ? |"]
r = w.outbox(done=["| 2026-11-24 | a.gcode | ? |", "| not | in | it |", 7])
assert r["removed"] == 1 and r["rows"] == ["| 2026-11-24 | b.gcode | ? |"], r
text = open(f).read()
assert "a.gcode" not in text and "b.gcode" in text and "footer" in text and "| Date |" in text
# two identical rows (the same file failing twice in a day), only one read and committed: the other stays
x = "| 2026-11-25 | c.gcode | ? |"
open(f, "w").write("| Date | Model (file) | For |\n| --- | --- | --- |\n" + x + "\n" + x + "\n")
r = w.outbox(done=[x])
assert r["removed"] == 1 and r["rows"] == [x] and open(f).read().count(x) == 1, r
assert not os.path.exists(f + ".part")
w.CFG["print_log"] = os.path.join(t, "none.md")
assert w.outbox()["rows"] == []
print("outbox check passed")
