"""From circuit to orderable board in one place. Every step needs KiCad 10; routing also needs Java 25 and Freerouting.

    python pcb.py all   hardware/<name>     circuit, layout, build, route, check, fab: the lot
    python pcb.py circuit hardware/<name>   run circuit.py (ERC, netlist, parts.json)
    python pcb.py build hardware/<name>     run layout.py, then build <name>.kicad_pcb with the parts placed
    python pcb.py route hardware/<name>     autoroute with Freerouting, add the ground pours
    python pcb.py check hardware/<name>     DRC (and ERC from circuit.py); removes dangling vias; exits 1 on any error
    python pcb.py schematic hardware/<name> a KiCad schematic laid out automatically (slow, randomised; 10 minute limit)
    python pcb.py fab   hardware/<name>     Gerbers, drill, BOM and placement for JLCPCB, a zip, STEP, STL, renders
    python pcb.py dims  hardware/<name>     board_dims.scad: outline, holes, LED positions, for the case (enclosure-from-pcb)

Freerouting: set FREEROUTING_JAR, or put freerouting-*.jar in ~/.local/share/freerouting/. Java: JAVA or PATH.
"""
import csv
import glob
import json
import os
import shutil
import subprocess
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kicad_env  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
NL = chr(10)
KCLI, KPY = kicad_env.KICAD_CLI, kicad_env.KICAD_PYTHON


def need(thing, why):
    if not thing:
        sys.exit(f"missing: {why}")
    return thing


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode:
        sys.exit(f"failed: {' '.join(map(str, cmd[:3]))}\n{(r.stdout + r.stderr)[-1500:]}")
    return r.stdout


def kicad_py(step, d):
    print(run([need(KPY, "KiCad's Python (install KiCad 10)"), os.path.join(HERE, "pcb_kicad.py"), step, d]).strip())


def java():
    cands = [os.environ.get("JAVA"), shutil.which("java")] + sorted(glob.glob(r"C:\Program Files\Eclipse Adoptium\jre-2[5-9]*\bin\java.exe"), reverse=True)
    for c in cands:
        if c and os.path.exists(c):
            v = subprocess.run([c, "-version"], capture_output=True, text=True).stderr
            if any(f'"{n}' in v for n in range(25, 40)):
                return c
    sys.exit("missing: Java 25 or newer (Freerouting 2.5 needs it)")


def names(d):
    name = os.path.basename(os.path.normpath(d))
    return name, os.path.join(d, name + ".kicad_pcb")


def circuit(d):
    out = run([sys.executable, os.path.join(d, "circuit.py")], cwd=d)
    erc = [l for l in out.splitlines() if l.startswith("ERC") or "parts," in l]
    print("\n".join(erc))
    return all("No errors or warnings" in l or "0 errors" in l for l in erc if l.startswith("ERC INFO"))


def schematic(d):
    # Separate from "all": the layout engine is randomised and can grind for a long time, so it never blocks the board.
    try:
        r = subprocess.run([sys.executable, os.path.join(d, "circuit.py")], cwd=d, capture_output=True, text=True,
                           env={**os.environ, "SCHEMATIC": "1"}, timeout=600)
        print(NL.join(l for l in r.stdout.splitlines() if "schematic" in l))
    except subprocess.TimeoutExpired:
        print("schematic NOT regenerated: the layout engine ran past 10 minutes; try again (it is randomised)")


def build(d):
    print(run([sys.executable, os.path.join(d, "layout.py")], cwd=d).strip())
    for f in glob.glob(os.path.join(d, "*.kicad_pr[ol]")):
        os.remove(f)   # rebuilt from board.json every time, so the rules can't drift
    kicad_py("new", d)


def route(d):
    name, _ = names(d)
    jar = os.environ.get("FREEROUTING_JAR") or next(iter(sorted(glob.glob(os.path.expanduser("~/.local/share/freerouting/freerouting-*.jar")), reverse=True)), None)
    need(jar, "Freerouting jar (FREEROUTING_JAR or ~/.local/share/freerouting/)")
    kicad_py("dsn", d)
    log = run([java(), "-jar", jar, "-de", f"{name}.dsn", "-do", f"{name}.ses", "-mp", "30", "--gui.enabled=false"], cwd=d)
    last = [l for l in log.splitlines() if "incomplete connections" in l]
    print("Freerouting:", last[-1].split("router score")[-1].strip() if last else "finished")
    kicad_py("ses", d)


def drc(d):
    _, pcb = names(d)
    rep = os.path.join(d, "drc.json")
    subprocess.run([need(KCLI, "kicad-cli"), "pcb", "drc", "--format", "json", "--severity-error", "--severity-warning",
                    "--refill-zones", "-o", rep, pcb], capture_output=True, text=True)
    return json.load(open(rep))


def check(d):
    r = drc(d)
    if any(v["type"] in ("via_dangling", "starved_thermal") for v in r["violations"]):
        kicad_py("tidy", d)
        r = drc(d)
    errors = [v for v in r["violations"] if v["severity"] == "error"]
    warns = [v for v in r["violations"] if v["severity"] == "warning"]
    kinds = {}
    for v in warns:
        kinds[v["type"]] = kinds.get(v["type"], 0) + 1
    print(f"DRC: {len(errors)} errors, {len(r['unconnected_items'])} unconnected, {len(warns)} warnings {kinds or ''}")
    for v in errors[:10]:
        print("  ", v["type"], "|", v["description"][:100])
    return not errors and not r["unconnected_items"]


def fab(d):
    name, pcb = names(d)
    out = os.path.join(d, "fab")
    shutil.rmtree(out, ignore_errors=True)
    g = os.path.join(out, "gerbers")
    os.makedirs(g)
    k = need(KCLI, "kicad-cli")
    run([k, "pcb", "export", "gerbers", "-o", g + os.sep, "--layers", "F.Cu,B.Cu,F.Paste,B.Paste,F.Silkscreen,B.Silkscreen,F.Mask,B.Mask,Edge.Cuts", pcb])
    run([k, "pcb", "export", "drill", "-o", g + os.sep, "--format", "excellon", "--excellon-separate-th", pcb])
    run([k, "pcb", "export", "pos", "-o", os.path.join(out, f"{name}-cpl.csv"), "--format", "csv", "--units", "mm", "--side", "both", pcb])
    # Bare copper (mounting holes, test pads): nothing to buy or place, so kept out of the BOM and the placement file
    parts = json.load(open(os.path.join(d, "parts.json")))
    bare = {p["ref"] for p in parts if p["footprint"].split(":")[0] in ("MountingHole", "TestPoint")}
    # JLCPCB's placement columns
    rows = [r for r in csv.DictReader(open(os.path.join(out, f"{name}-cpl.csv"), encoding="utf-8")) if r["Ref"] not in bare]
    with open(os.path.join(out, f"{name}-cpl-jlcpcb.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
        for r in rows:
            w.writerow([r["Ref"], r["PosX"] + "mm", r["PosY"] + "mm", "Top" if r["Side"] == "top" else "Bottom", r["Rot"]])
    # BOM grouped by value and footprint, with LCSC numbers, in JLCPCB's columns
    groups = {}
    for p in parts:
        if p["ref"] in bare:
            continue
        key = (p["value"], p["footprint"].split(":")[1], p.get("lcsc", ""))
        groups.setdefault(key, []).append(p["ref"])
    with open(os.path.join(out, f"{name}-bom-jlcpcb.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Comment", "Designator", "Footprint", "LCSC Part #"])
        for (val, fp, lcsc), refs in sorted(groups.items()):
            w.writerow([val, ",".join(sorted(refs, key=lambda r: (r[0], int("".join(c for c in r if c.isdigit()) or 0)))), fp, lcsc])
    with zipfile.ZipFile(os.path.join(out, f"{name}-gerbers.zip"), "w", zipfile.ZIP_DEFLATED) as z:
        for f in os.listdir(g):
            z.write(os.path.join(g, f), f)
    run([k, "pcb", "export", "step", "-o", os.path.join(out, f"{name}.step"), "--subst-models", "--force", pcb])
    run([k, "pcb", "export", "stl", "-o", os.path.join(out, f"{name}.stl"), "--subst-models", "--force", pcb])
    for side in ("top", "bottom"):
        run([k, "pcb", "render", "--side", side, "--width", "2400", "--height", "640", "--zoom", "3.8", "--quality", "high",
             "--background", "opaque", "-o", os.path.join(out, f"{name}-{side}.png"), pcb])
    run([k, "pcb", "render", "--width", "2000", "--height", "1100", "--zoom", "1.7", "--rotate", "-35,0,22", "--quality", "high",
         "--background", "opaque", "-o", os.path.join(out, f"{name}-3d.png"), pcb])
    missing = sorted(r for (_, _, lcsc), refs in groups.items() if not lcsc for r in refs)
    print(f"fab files in {out}: gerbers zip, BOM, placement, STEP, STL, renders")
    if missing:
        print("  BOM lines with no LCSC number yet (pick them in JLCPCB's parts search):", ", ".join(missing[:12]) + (" ..." if len(missing) > 12 else ""))


def dims(d):
    """Write the board's numbers as OpenSCAD variables, so the case is built from the same figures as the board."""
    b = json.load(open(os.path.join(d, "board.json")))
    holes = [b["place"][k][:2] for k in sorted(b["place"]) if k.startswith("H")]
    leds = sorted(b["place"][k][:2] for k in b["place"] if k.startswith("D") and k[1:].isdigit())
    lines = [f"// Written by tools/pcb.py dims from board.json. Do not edit: change layout.py and rerun.",
             f"board_w = {b['width']}; board_h = {b['height']}; board_t = 1.6;",
             f"board_holes = {json.dumps(holes)};   // [x, y] from the board's top-left, y down",
             f"board_leds = {json.dumps(leds)};",
             f"board_buttons = {json.dumps(sorted(b['place'][k][:2] for k in b['place'] if k.startswith('SW')))};   // pushed through the case with a pin"]
    if "tallest" in b:
        lines.append(f"board_tallest = {b['tallest']};   // mm above the board's top side")
    open(os.path.join(d, "board_dims.scad"), "w", newline=NL).write(NL.join(lines) + NL)
    print(f"board_dims.scad: {len(holes)} holes, {len(leds)} LEDs")


def main():
    if len(sys.argv) < 3 or sys.argv[1] not in ("all", "circuit", "schematic", "build", "route", "check", "fab", "dims"):
        sys.exit(__doc__)
    step, d = sys.argv[1], os.path.abspath(sys.argv[2])
    if step in ("all", "circuit") and not circuit(d):
        sys.exit("ERC has errors: fix circuit.py first")
    if step == "schematic":
        schematic(d)
    if step in ("all", "build"):
        build(d)
    if step in ("all", "route"):
        route(d)
    if step in ("all", "check") and not check(d):
        sys.exit(1)
    if step in ("all", "fab"):
        fab(d)
    if step in ("all", "dims"):
        dims(d)


if __name__ == "__main__":
    main()
