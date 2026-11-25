"""printer.py resume follows Workshop's rule: not while the printer won't take it, and after its detector paused the
print only with --person-checked.  python tools/test_cli_resume.py  (a pretend printer: no real command is sent)"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import printer as p  # noqa: E402
import json  # noqa: E402
import tempfile  # noqa: E402

p.CONF = os.path.join(tempfile.mkdtemp(), "printer.json")

posted, state = [], {"exception": {"id": 532}}
p.load_conf = lambda: {"host": "pretend"}
p.query = lambda *objs: {"print_stats": {"state": "paused", **state}, "pause_resume": {"is_paused": True},
                         "machine_state_manager": {"main_state": 0, "action_code": 0}}
p.hold = lambda s: {"can_resume": True, "why": "spaghetti", "waiting": ""}
p.call = lambda path, method="GET", *a, **k: posted.append(path) or "ok"
p.record = lambda *a, **k: None
p.status = lambda: {"file": "a.gcode"}


def run(*args):
    sys.argv = ["printer.py", "--printer", "pretend", "resume", *args]
    try:
        p.main()
        return 0
    except SystemExit as e:
        return e.code


assert "person-checked" in str(run()) and not posted        # detector pause, nobody looked: refused
assert run("--person-checked") == 0 and posted == ["/printer/print/resume"]
posted.clear()
p.hold = lambda s: {"can_resume": False, "why": "", "waiting": "the bed is still scanning"}
assert "scanning" in str(run("--person-checked")) and not posted
p.hold = lambda s: {"can_resume": True, "why": "", "waiting": ""}
state.clear()
assert run() == 0 and posted == ["/printer/print/resume"]     # a person's own pause: a plain resume
# Workshop's photo watch paused it (no detector code): the CLI reads its unanswered pauses too
json.dump({"run": ["spaghetti", 40, False]}, open(os.path.join(os.path.dirname(p.CONF), "watch-paused.json"), "w"))
posted.clear()
assert "photo watch" in str(run()) and not posted
assert run("--person-checked") == 0 and posted == ["/printer/print/resume"]
print("cli resume check passed")
