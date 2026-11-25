"""printer.py unload against a pretend U1, no printer and no real commands.  python tools/test_unload.py

Two firmware behaviours: prepare only heats (doing then unloads), and prepare already unloads (21 Nov, T2 on stock
2.1.2: a doing sent then was refused as state_mismatch). The routine must send doing only to a head still loaded."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import printer  # noqa: E402

printer.time.sleep = lambda s: None


class FakeU1:
    def __init__(self, prepare_unloads):
        self.prepare_unloads, self.channel, self.sent, self.target = prepare_unloads, "load_finish", [], 0

    def call(self, path, method="GET", body=None, timeout=15, raw=False):
        cmd = printer.urllib.parse.unquote(path.split("script=", 1)[1]) if "script=" in path else path
        self.sent.append(cmd)
        if "STAGE=prepare" in cmd:
            self.target = 240
            if self.prepare_unloads:
                self.channel = "unload_finish"
        elif "STAGE=doing" in cmd:
            if self.channel != "load_finish":
                raise printer.urllib.error.HTTPError(path, 400, "state_mismatch", {}, None)   # what the U1 does
            self.channel = "unload_finish"
        return {}

    def query(self, *objs):
        o = objs[0]
        if o == "print_stats":
            return {o: {"state": "complete"}}
        if o == "print_task_config":
            return {o: {"filament_exist": [1] * 4, "filament_type": ["PETG"] * 4, "filament_sub_type": ["generic"] * 4,
                        "filament_soft": [0] * 4}}
        if o == "machine_state_manager":
            return {o: {"main_state": 0}}
        if o.startswith("filament_feed"):
            return {o: {f"extruder{i}": {"channel_state": self.channel if i == 2 else "load_finish"} for i in range(4)}}
        return {o: {"target": self.target, "temperature": self.target}}


for prepare_unloads in (False, True):
    u1 = FakeU1(prepare_unloads)
    printer.call, printer.query = u1.call, u1.query
    printer.unload("T2")
    doings = [c for c in u1.sent if "STAGE=doing" in c]
    assert u1.channel == "unload_finish", (prepare_unloads, u1.channel)
    assert len(doings) == (0 if prepare_unloads else 1), (prepare_unloads, u1.sent)
    assert u1.sent[0].endswith("ACTION=START") and u1.sent[-1].endswith("ACTION=STOP"), u1.sent
print("unload checks passed (prepare that only heats, and prepare that already unloads)")
