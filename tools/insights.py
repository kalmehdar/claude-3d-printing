"""The numbers behind Workshop's planner, spools, cost and health views. Pure functions, standard library only.

    plan(queue, now, busy_until, quiet)      when each queued job would start and end; flags starts in the quiet hours
    spools(spoolman, heads)                  Spoolman's spools per head: grams left, low under 200 g
    costs(jobs, prices, starters)            filament and cost per print, per month and per person
    mesh(bed_mesh)                           the probed bed mesh as rows of heights, with range and flatness
"""
import datetime as dt

LOW_G = 200          # the Almehdar register flags a reorder under 200 g
QUIET = (3, 8)       # Khalid, 22 Nov 2026: no new start from 03:00 (until 08:00); a print already running may finish
DENSITY = {"PLA": 1.24, "PETG": 1.27, "TPU": 1.21, "ABS": 1.04}   # g/cm3; PETG and PLA as the Almehdar profiles carry
AREA = 3.14159265 * (1.75 / 2) ** 2 / 1000                       # cm2 per mm of 1.75 mm filament, as cm3 per mm


def in_quiet(t, quiet=QUIET):
    """True when the local time t (a datetime) falls in the quiet hours [start, end)."""
    a, b = quiet
    h = t.hour + t.minute / 60
    return a <= h < b if a < b else h >= a or h < b


def plan(queue, now, busy_until=None, quiet=QUIET, gap_s=300):
    """[{file, seconds, start, end, quiet_start}] for queued jobs run back to back after the current print.
    queue: [{"filename", "seconds"}] (seconds = the slicer's estimate, corrected by the caller); now and busy_until:
    datetimes. A job whose start lands in the quiet hours is flagged: the queue would hold it until morning."""
    t, out = max(now, busy_until or now), []
    for j in queue:
        start = t if t == now and not busy_until else t + dt.timedelta(seconds=gap_s)   # a few minutes to clear the bed
        end = start + dt.timedelta(seconds=j.get("seconds") or 0)
        out.append({"file": j["filename"], "seconds": j.get("seconds"), "start": start, "end": end, "quiet_start": in_quiet(start, quiet)})
        t = end
    return out


def spools(spoolman, heads=None):
    """Rows for each Spoolman spool, in head order when heads maps "T0".. to spool ids (Spoolman's /api/v1/spool list)."""
    by_id = {s["id"]: s for s in spoolman}
    order = [(h, heads[h]) for h in sorted(heads)] if heads else [(None, s["id"]) for s in spoolman]
    rows = []
    for head, sid in order:
        s = by_id.get(sid)
        if not s:
            continue
        f = s.get("filament") or {}
        left = s.get("remaining_weight")
        rows.append({"head": head, "id": sid, "name": " ".join(x for x in ((f.get("vendor") or {}).get("name"), f.get("material"), f.get("name")) if x),
                     "colour": "#" + (f.get("color_hex") or "888888").lstrip("#")[:6], "left_g": round(left) if left is not None else None,
                     "used_g": round(s.get("used_weight") or 0), "low": left is not None and left < LOW_G, "archived": bool(s.get("archived"))})
    return rows


def grams(mm, material):
    return mm * AREA * DENSITY.get(str(material).upper().split(";")[0], 1.25)


def costs(jobs, prices, starters=None):
    """Per print, per month and per person, from Moonraker history jobs. prices: {MATERIAL: £ per kg};
    starters: {(filename, start_time rounded to the minute): who} from the activity log (who pressed start)."""
    starters = starters or {}
    rows, months, people = [], {}, {}
    for j in jobs:
        if not j.get("filament_used"):
            continue
        mat = str((j.get("metadata") or {}).get("filament_type") or "").split(";")[0].upper()
        g = grams(j["filament_used"], mat)
        c = g * prices.get(mat, 0) / 1000
        when = dt.datetime.fromtimestamp(j.get("start_time") or 0)
        who = starters.get((j.get("filename"), int((j.get("start_time") or 0) // 60)), "someone")
        rows.append({"file": j.get("filename"), "when": when, "status": j.get("status"), "grams": round(g, 1), "cost": round(c, 2), "who": who})
        m = months.setdefault(when.strftime("%Y-%m"), {"prints": 0, "grams": 0.0, "cost": 0.0})
        p = people.setdefault(who, {"prints": 0, "grams": 0.0, "cost": 0.0})
        for d in (m, p):
            d["prints"] += 1
            d["grams"] = round(d["grams"] + g, 1)
            d["cost"] = round(d["cost"] + c, 2)
    return {"prints": rows, "months": months, "people": people}


def mesh(bed_mesh):
    """The probed mesh (Klipper's bed_mesh object): rows of heights in mm, min, max, range and a plain verdict."""
    m = (bed_mesh or {}).get("probed_matrix") or []
    vals = [v for r in m for v in r]
    if not vals:
        return {"rows": [], "range": None, "verdict": "no mesh yet"}
    lo, hi = min(vals), max(vals)
    rng = round(hi - lo, 3)
    verdict = "flat" if rng <= 0.15 else "fine" if rng <= 0.3 else "uneven: check the plate sits flat and is clean underneath"
    return {"rows": m, "min": round(lo, 3), "max": round(hi, 3), "range": rng, "verdict": verdict,
            "x": (bed_mesh.get("mesh_min") or [None])[0], "y": (bed_mesh.get("mesh_max") or [None, None])[1]}


if __name__ == "__main__":   # self-checks
    D = dt.datetime
    assert in_quiet(D(2026, 11, 22, 3, 0)) and in_quiet(D(2026, 11, 22, 7, 59)) and not in_quiet(D(2026, 11, 22, 8, 0))
    assert not in_quiet(D(2026, 11, 22, 2, 59)) and in_quiet(D(2026, 11, 22, 23, 30), (23, 7))
    p = plan([{"filename": "a.gcode", "seconds": 3600}, {"filename": "b.gcode", "seconds": 1800}],
             now=D(2026, 11, 22, 1, 0), busy_until=D(2026, 11, 22, 1, 30))
    assert p[0]["start"] == D(2026, 11, 22, 1, 35) and p[0]["end"] == D(2026, 11, 22, 2, 35) and not p[0]["quiet_start"], p[0]
    assert p[1]["start"] == D(2026, 11, 22, 2, 40) and not p[1]["quiet_start"]   # starts 02:40: allowed, may run past 03:00
    p2 = plan([{"filename": "c.gcode", "seconds": 60}], now=D(2026, 11, 22, 2, 58), busy_until=D(2026, 11, 22, 3, 0))
    assert p2[0]["quiet_start"]   # would start 03:05: the queue holds it until 08:00
    sp = spools([{"id": 2, "remaining_weight": 150.4, "used_weight": 829.6, "filament": {"material": "PETG", "name": "Black", "color_hex": "1c1c1c", "vendor": {"name": "SUNLU"}}},
                 {"id": 1, "remaining_weight": 1000, "filament": {"material": "PETG", "name": "White", "color_hex": "f2f2f0"}}],
                {"T0": 1, "T1": 2})
    assert [r["head"] for r in sp] == ["T0", "T1"] and sp[1]["low"] and not sp[0]["low"] and sp[1]["name"] == "SUNLU PETG Black", sp
    c = costs([{"filename": "x.gcode", "filament_used": 10000, "start_time": 1795000000, "status": "completed", "metadata": {"filament_type": "PETG;PETG"}}],
              {"PETG": 12.0}, {("x.gcode", 1795000000 // 60): "Mohamed"})
    assert c["prints"][0]["who"] == "Mohamed" and abs(c["prints"][0]["grams"] - 30.5) < 0.2 and c["people"]["Mohamed"]["prints"] == 1, c
    mm = mesh({"probed_matrix": [[0.0, 0.1], [0.05, -0.1]], "mesh_min": [10, 10], "mesh_max": [260, 260]})
    assert mm["range"] == 0.2 and mm["verdict"] == "fine", mm
    assert mesh({})["verdict"] == "no mesh yet"
    print("insights checks passed")
