"""What the printer needs next, from what it already counts. Read-only, except `done`.

    python maintenance.py [status] [--json]      print hours, head swaps against the maker's limit, low spools, manual jobs due
    python maintenance.py done TASK [--note ...]  record a job done now (shafts, carbon, belts, leadscrews, pins, pogo, plate)

The printer (Moonraker) counts print time and filament; a Snapmaker U1 also counts head swaps per head against its own
maintenance limit (individual_maintenance_threshold). Spool weights come from Spoolman when "spoolman" is set in
printer.json. The jobs follow Snapmaker's U1 core components maintenance guide (monthly, quarterly, every 4-6 months),
counted from the first print until done once; change an interval in ~/.config/claude-3d-printing/maintenance.json.
Standard library only.
"""
import argparse
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import printer  # noqa: E402

FILE = os.path.join(os.path.dirname(printer.CONF), "maintenance.json")
# Snapmaker's U1 core components maintenance guide (wiki.snapmaker.com, read 21 Nov 2026): monthly, quarterly, 4-6 months.
# The U1 wipes its own nozzles, so no nozzle job. The plate wash is ours. Change any interval in maintenance.json.
TASKS = {
    "shafts": {"what": "clean the linear shafts and bearings, then a thin film of white lithium grease (NLGI 2-3), never WD-40", "every_days": 30},
    "carbon": {"what": "wipe the X carbon rods with isopropanol; never grease them", "every_days": 30},
    "belts": {"what": "check the XY belt tension (the screen says if needed) and wipe the teeth dry", "every_days": 30},
    "leadscrews": {"what": "brush the Z lead screws clean, then a thin layer of white lithium grease", "every_days": 90},
    "pins": {"what": "clean the steel pins and balls, then grease the marked spots", "every_days": 90},
    "pogo": {"what": "wipe the pogo pins with alcohol, one direction only, then let them dry", "every_days": 120},
    "plate": {"what": "wash the plate with soap and water (ours, not Snapmaker's)", "every_prints": 15},
}
UNITS = ("days", "hours", "prints")
LOW_SPOOL_G = 200   # the almehdar reorder line


def load():
    try:
        saved = json.load(open(FILE, encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    tasks = {k: {**v, **saved.get("tasks", {}).get(k, {})} for k, v in TASKS.items()}
    return {"tasks": tasks, "done": saved.get("done", {})}


def due(tasks, done, now, start):
    """[(task, what, left, unit)] with left <= 10% of the interval or overdue, most overdue first.
    now and start are {"days": ..., "hours": ..., "prints": ...}; a job never done counts from start (the first print)."""
    out = []
    for k, t in tasks.items():
        unit = next((u for u in UNITS if t.get("every_" + u)), None)
        if not unit:
            continue
        every = t["every_" + unit]
        since = done.get(k, {}).get(unit, start[unit])
        left = every - (now[unit] - since)
        if left <= every * 0.1:
            out.append((k, t["what"], round(left, 1), unit))
    return sorted(out, key=lambda x: x[2])


def heads(status, settings):
    """[(head, swaps since maintenance, limit)] for printers that count head swaps (Snapmaker U1)."""
    out = []
    for name, s in sorted(status.items()):
        if "switch_count" not in s:
            continue
        limit = settings.get(name, {}).get("individual_maintenance_threshold") or settings.get("extruder", {}).get("individual_maintenance_threshold")
        out.append((name, s["switch_count"] - s.get("last_maintenance_count", 0), limit))
    return out


def spools(url):
    if not url:
        return None
    with urllib.request.urlopen(url.rstrip("/") + "/api/v1/spool", timeout=10) as r:
        return [(s["id"], f'{s["filament"].get("vendor", {}).get("name", "")} {s["filament"]["name"]}'.strip(),
                 round(s.get("remaining_weight") or 0)) for s in json.load(r)]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0], formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("cmd", nargs="?", default="status", choices=["status", "done"])
    ap.add_argument("task", nargs="?")
    ap.add_argument("--note", default="")
    ap.add_argument("--json", action="store_true")
    conf = printer.load_conf()
    ap.add_argument("--printer", default=os.environ.get("PRINTER_HOST") or conf.get("host"))
    a = ap.parse_args()
    if not a.printer:
        sys.exit(f"no printer: pass --printer, set PRINTER_HOST, or write {{\"host\": \"...\"}} to {printer.CONF}")
    printer.BASE = a.printer if a.printer.startswith("http") else f"http://{a.printer}:7125"
    totals = printer.call("/server/history/totals")["job_totals"]
    hours = round(totals["total_print_time"] / 3600, 1)
    prints = int(totals["total_jobs"])
    today = round(time.time() / 86400, 1)
    first = printer.call("/server/history/list?limit=1&order=asc").get("jobs") or [{"start_time": time.time()}]
    now, start = {"days": today, "hours": hours, "prints": prints}, {"days": round(first[0]["start_time"] / 86400, 1), "hours": 0, "prints": 0}
    data = load()
    if a.cmd == "done":
        if a.task not in data["tasks"]:
            sys.exit(f"no task {a.task!r}: {', '.join(data['tasks'])}")
        saved = json.load(open(FILE, encoding="utf-8")) if os.path.exists(FILE) else {}
        saved.setdefault("done", {})[a.task] = {**now, "at": time.strftime("%Y-%m-%d %H:%M"), "note": a.note}
        os.makedirs(os.path.dirname(FILE), exist_ok=True)
        json.dump(saved, open(FILE, "w", encoding="utf-8"), indent=1)
        print(f"{a.task} done at {hours} print hours, {prints} prints")
        return
    objs = [o for o in printer.call("/printer/objects/list")["objects"] if o.startswith("extruder")]
    status = printer.query(*objs) if objs else {}
    settings = printer.query("configfile")["configfile"].get("settings", {}) if objs else {}
    report = {"print_hours": hours, "prints": prints, "filament_m": round(totals["total_filament_used"] / 1000, 1),
              "heads": heads(status, settings), "due": due(data["tasks"], data["done"], now, start)}
    try:
        report["spools"] = spools(conf.get("spoolman") or os.environ.get("SPOOLMAN_URL"))
    except OSError as e:
        report["spools"] = f"Spoolman unreachable: {e}"
    if a.json:
        print(json.dumps(report, indent=1))
        return
    print(f"{hours} print hours, {prints} prints, {report['filament_m']} m of filament")
    for h, n, limit in report["heads"]:
        print(f"  {h}: {n} head swaps since maintenance" + (f" of {limit:g} ({100 * n / limit:.1f}%)" if limit else ""))
    for k, what, left, unit in report["due"]:
        print(f"  DUE {k}: {what} ({'overdue by ' + str(-left) if left < 0 else str(left) + ' left'} {unit}); when done: maintenance.py done {k}")
    low = [s for s in report["spools"] if s[2] < LOW_SPOOL_G] if isinstance(report["spools"], list) else []
    for i, name, g in low:
        print(f"  LOW spool {i} {name}: {g} g left (reorder under {LOW_SPOOL_G} g)")
    if isinstance(report["spools"], str):
        print("  " + report["spools"])
    if not report["due"] and not low:
        print("  nothing due")


if __name__ == "__main__":
    main()
