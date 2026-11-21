"""What the printer needs next, from what it already counts. Read-only, except `done`.

    python maintenance.py [status] [--json]      print hours, head swaps against the maker's limit, low spools, jobs due
    python maintenance.py done TASK [--note ...]  record a job done now (shafts, carbon, belts, leadscrews, pins, pogo, plate)
    python maintenance.py status --ntfy URL       also post the report (with this week's prints) to an ntfy topic URL

The printer (Moonraker) counts print time and filament; a Snapmaker U1 also counts head swaps per head against its own
maintenance limit (individual_maintenance_threshold). Spool weights come from Spoolman when "spoolman" is set in
printer.json. The jobs follow Snapmaker's U1 core components maintenance guide (monthly, quarterly, every 4-6 months),
counted from the first print until done once. What was done, and any changed interval, is kept in the printer's own
Moonraker database (namespace "claude_maintenance"), so every machine that runs this sees the same record.
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

NAMESPACE = "claude_maintenance"   # Moonraker's database on the printer: shared by every machine that runs this
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


def db_get(key):
    try:
        return printer.call(f"/server/database/item?namespace={NAMESPACE}&key={key}")["value"]
    except OSError:   # 404: nothing stored yet
        return {}


def db_set(key, value):
    req = urllib.request.Request(printer.BASE + "/server/database/item", method="POST", headers={"Content-Type": "application/json"},
                                 data=json.dumps({"namespace": NAMESPACE, "key": key, "value": value}).encode())
    urllib.request.urlopen(req, timeout=15).close()


def load():
    tasks = {k: {**v, **db_get("tasks").get(k, {})} for k, v in TASKS.items()}
    return {"tasks": tasks, "done": db_get("done")}


def week(jobs, now_s):
    """(prints, hours, metres) of jobs that started in the last 7 days."""
    recent = [j for j in jobs if j.get("start_time", 0) > now_s - 7 * 86400 and j.get("status") != "in_progress"]   # as the totals: finished jobs
    return len(recent), round(sum(j.get("print_duration", 0) for j in recent) / 3600, 1), round(sum(j.get("filament_used", 0) for j in recent) / 1000, 1)


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
    ap.add_argument("--ntfy", default=os.environ.get("MAINTENANCE_NTFY"), help="ntfy topic URL to post the report to")
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
        done = data["done"]
        done[a.task] = {**now, "at": time.strftime("%Y-%m-%d %H:%M"), "note": a.note}
        db_set("done", done)
        print(f"{a.task} done at {hours} print hours, {prints} prints (kept on the printer)")
        return
    objs = [o for o in printer.call("/printer/objects/list")["objects"] if o.startswith("extruder")]
    status = printer.query(*objs) if objs else {}
    settings = printer.query("configfile")["configfile"].get("settings", {}) if objs else {}
    jobs = printer.call("/server/history/list?limit=200&order=desc").get("jobs", [])
    report = {"print_hours": hours, "prints": prints, "filament_m": round(totals["total_filament_used"] / 1000, 1),
              "week": week(jobs, time.time()),
              "heads": heads(status, settings), "due": due(data["tasks"], data["done"], now, start)}
    try:
        report["spools"] = spools(conf.get("spoolman") or os.environ.get("SPOOLMAN_URL"))
    except OSError as e:
        report["spools"] = f"Spoolman unreachable: {e}"
    if a.json:
        print(json.dumps(report, indent=1))
        return
    wp, wh, wm = report["week"]
    lines = [f"This week: {wp} prints, {wh} h, {wm} m of filament. In all: {hours} h, {prints} prints, {report['filament_m']} m."]
    lines += [f"{h}: {n} head swaps since maintenance" + (f" of {limit:g} ({100 * n / limit:.1f}%)" if limit else "")
              for h, n, limit in report["heads"]]
    lines += [f"DUE {k}: {what} ({'overdue by ' + str(-left) if left < 0 else str(left) + ' left'} {unit}); when done: maintenance.py done {k}"
              for k, what, left, unit in report["due"]]
    low = [s for s in report["spools"] if s[2] < LOW_SPOOL_G] if isinstance(report["spools"], list) else []
    lines += [f"LOW spool {i} {name}: {g} g left (reorder under {LOW_SPOOL_G} g)" for i, name, g in low]
    if isinstance(report["spools"], str):
        lines.append(report["spools"])
    if not report["due"] and not low:
        lines.append("Nothing due.")
    text = "\n".join(lines)
    print(text)
    if a.ntfy:
        title = "U1: " + ("maintenance due" if report["due"] else "spool low" if low else "weekly summary")
        req = urllib.request.Request(a.ntfy, data=text.encode(), method="POST",
                                     headers={"Title": title, "Tags": "wrench" if report["due"] or low else "printer"})
        urllib.request.urlopen(req, timeout=15).close()
        print("posted to ntfy")   # never echo the topic URL


if __name__ == "__main__":
    main()
