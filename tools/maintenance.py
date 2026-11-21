"""What the printer needs next, from what it already counts. Read-only, except `done`.

    python maintenance.py [status] [--json]      print hours, head swaps against the maker's limit, low spools, manual jobs due
    python maintenance.py done TASK [--note ...]  record a manual job done now (nozzle, rods, plate, belts)

The printer (Moonraker) counts print time and filament; a Snapmaker U1 also counts head swaps per head against its own
maintenance limit (individual_maintenance_threshold). Spool weights come from Spoolman when "spoolman" is set in
printer.json. The manual jobs and their intervals live in ~/.config/claude-3d-printing/maintenance.json: change an
interval there; the defaults are starting points, not the maker's figures. Standard library only.
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
# ponytail: starting intervals in print hours (plate in prints); tune them in maintenance.json once the printer shows wear
TASKS = {
    "nozzle": {"what": "clean the nozzles and wipers", "every_hours": 50},
    "rods": {"what": "clean and lube the rods and rails", "every_hours": 200},
    "belts": {"what": "check belt tension", "every_hours": 300},
    "plate": {"what": "wash the plate with soap and water", "every_prints": 15},
}
LOW_SPOOL_G = 200   # the almehdar reorder line


def load():
    try:
        saved = json.load(open(FILE, encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    tasks = {k: {**v, **saved.get("tasks", {}).get(k, {})} for k, v in TASKS.items()}
    return {"tasks": tasks, "done": saved.get("done", {})}


def due(tasks, done, hours, prints):
    """[(task, what, left, unit)] with left <= 10% of the interval or overdue, most overdue first."""
    out = []
    for k, t in tasks.items():
        unit, every, now = ("hours", t.get("every_hours"), hours) if t.get("every_hours") else ("prints", t.get("every_prints"), prints)
        if not every:
            continue
        since = done.get(k, {}).get(unit, 0)
        left = every - (now - since)
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
    data = load()
    if a.cmd == "done":
        if a.task not in data["tasks"]:
            sys.exit(f"no task {a.task!r}: {', '.join(data['tasks'])}")
        saved = json.load(open(FILE, encoding="utf-8")) if os.path.exists(FILE) else {}
        saved.setdefault("done", {})[a.task] = {"hours": hours, "prints": prints, "at": time.strftime("%Y-%m-%d %H:%M"), "note": a.note}
        os.makedirs(os.path.dirname(FILE), exist_ok=True)
        json.dump(saved, open(FILE, "w", encoding="utf-8"), indent=1)
        print(f"{a.task} done at {hours} print hours, {prints} prints")
        return
    objs = [o for o in printer.call("/printer/objects/list")["objects"] if o.startswith("extruder")]
    status = printer.query(*objs) if objs else {}
    settings = printer.query("configfile")["configfile"].get("settings", {}) if objs else {}
    report = {"print_hours": hours, "prints": prints, "filament_m": round(totals["total_filament_used"] / 1000, 1),
              "heads": heads(status, settings), "due": due(data["tasks"], data["done"], hours, prints)}
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
        print(f"  DUE {k}: {what} ({'overdue by ' + str(-left) if left < 0 else str(left) + ' left'} {unit}); then: maintenance.py done {k}")
    low = [s for s in report["spools"] if s[2] < LOW_SPOOL_G] if isinstance(report["spools"], list) else []
    for i, name, g in low:
        print(f"  LOW spool {i} {name}: {g} g left (reorder under {LOW_SPOOL_G} g)")
    if isinstance(report["spools"], str):
        print("  " + report["spools"])
    if not report["due"] and not low:
        print("  nothing due")


if __name__ == "__main__":
    main()
