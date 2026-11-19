"""Board steps that need KiCad's own Python (pcbnew). tools/pcb.py runs this; you normally don't call it directly.

    <kicad python> pcb_kicad.py new   <dir>     build <name>.kicad_pcb from parts.json and board.json
    <kicad python> pcb_kicad.py dsn   <dir>     export the board for Freerouting (<name>.dsn)
    <kicad python> pcb_kicad.py ses   <dir>     import the routed result (<name>.ses), add the ground pours, fill them
"""
import json
import os
import sys

import pcbnew

MM = pcbnew.FromMM
FP_DIR = os.environ.get("KICAD10_FOOTPRINT_DIR") or os.path.join(os.path.dirname(sys.executable), "..", "share", "kicad", "footprints")
ORIGIN = (50.0, 50.0)   # board's top-left on the KiCad page, so nothing sits at negative coordinates


def P(x, y):
    return pcbnew.VECTOR2I(MM(ORIGIN[0] + x), MM(ORIGIN[1] + y))


def paths(d):
    name = os.path.basename(os.path.normpath(d))
    return name, os.path.join(d, name + ".kicad_pcb")


def new(d):
    name, pcb = paths(d)
    parts = json.load(open(os.path.join(d, "parts.json")))
    spec = json.load(open(os.path.join(d, "board.json")))
    board = pcbnew.CreateEmptyBoard()
    ds = board.GetDesignSettings()
    r = spec["rules"]
    ds.m_TrackMinWidth = MM(min(r["track"], 0.2))
    ds.m_MinClearance = MM(r["clearance"])
    ds.m_ViasMinSize = MM(r["via"])
    ds.m_MinThroughDrill = MM(r["drill"])
    ds.m_CopperEdgeClearance = MM(r["edge_clearance"])
    nc = ds.m_NetSettings.GetDefaultNetclass()
    nc.SetTrackWidth(MM(r["track"]))
    nc.SetClearance(MM(r["clearance"]))
    nc.SetViaDiameter(MM(r["via"]))
    nc.SetViaDrill(MM(r["drill"]))
    nets = {}

    def net(n):
        if n not in nets:
            ni = pcbnew.NETINFO_ITEM(board, n)
            board.Add(ni)
            nets[n] = ni
        return nets[n]

    missing = []
    for p in parts:
        lib, fpname = p["footprint"].split(":")
        fp = pcbnew.FootprintLoad(os.path.join(FP_DIR, lib + ".pretty"), fpname)
        if fp is None:
            sys.exit(f"footprint not found: {p['footprint']}")
        fp.SetReference(p["ref"])
        fp.SetValue(p["value"])
        if p.get("lcsc"):   # kept as a hidden field on the fab layer: it belongs in the BOM, not printed on the board
            fp.SetField("LCSC", p["lcsc"])
            for f in fp.GetFields():
                if f.GetName() == "LCSC":
                    f.SetVisible(False)
                    f.SetLayer(pcbnew.F_Fab)
        if p["ref"] in spec["place"]:
            x, y, rot = spec["place"][p["ref"]][:3]
            fp.SetPosition(P(x, y))
            fp.SetOrientationDegrees(rot)
        else:
            missing.append(p["ref"])
        board.Add(fp)
        for pad in fp.Pads():
            n = p["pins"].get(pad.GetNumber())
            if n and n not in ("None", "__NOCONNECT") and not n.startswith("NC_"):   # SKiDL's no-connect net is NOT a real net
                pad.SetNet(net(n))
    # outline
    w, h = spec["width"], spec["height"]
    for a, b in (((0, 0), (w, 0)), ((w, 0), (w, h)), ((w, h), (0, h)), ((0, h), (0, 0))):
        s = pcbnew.PCB_SHAPE(board)
        s.SetShape(pcbnew.SHAPE_T_SEGMENT)
        s.SetStart(P(*a))
        s.SetEnd(P(*b))
        s.SetLayer(pcbnew.Edge_Cuts)
        s.SetWidth(MM(0.1))
        board.Add(s)
    # keep tracks off the board edge: thin no-copper strips the router (Freerouting) respects
    e = r["edge_clearance"]
    for x0, y0, x1, y1 in ((0, 0, w, e), (0, h - e, w, h), (0, 0, e, h), (w - e, 0, w, h)):
        ka = pcbnew.ZONE(board)
        ka.SetIsRuleArea(True)
        ka.SetDoNotAllowTracks(True)
        ka.SetDoNotAllowVias(True)
        ka.SetDoNotAllowZoneFills(False)
        ka.SetDoNotAllowPads(False)
        ka.SetDoNotAllowFootprints(False)
        ka.SetLayerSet(pcbnew.LSET.AllCuMask())
        ol = ka.Outline()
        ol.NewOutline()
        for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
            ol.Append(P(x, y))
        board.Add(ka)
    # small-part reference labels go on the fab layer only: the dense rows stay readable, assembly drawings keep them
    for fp in board.GetFootprints():
        if fp.GetReference()[:1] in ("C", "R", "D") or fp.GetReference().startswith("H"):
            fp.Reference().SetLayer(pcbnew.F_Fab)
        for pad in fp.Pads():   # through-hole shell pads (USB-C) join the ground pour solidly, no starved thermals
            if pad.GetAttribute() == pcbnew.PAD_ATTRIB_PTH and pad.GetNetname() == spec.get("ground_pour"):
                pad.SetLocalZoneConnection(pcbnew.ZONE_CONNECTION_FULL)
    pcbnew.SaveBoard(pcb, board)
    write_project(d, name, spec)
    print(f"{pcb}: {len(parts)} parts, {len(nets)} nets" + (f"; NOT PLACED: {', '.join(missing)}" if missing else ""))


def write_project(d, name, spec):
    """KiCad 7+ keeps net classes and rules in the project file, not the board: without this the router never sees them."""
    r = spec["rules"]
    pro_path = os.path.join(d, name + ".kicad_pro")
    pro = json.load(open(pro_path)) if os.path.exists(pro_path) else {}
    rules = pro.setdefault("board", {}).setdefault("design_settings", {}).setdefault("rules", {})
    rules.update({"min_track_width": r.get("min_track", 0.2), "min_clearance": r["clearance"], "min_via_diameter": r["via"] - 0.1,
                  "min_through_hole_diameter": r.get("min_drill", r["drill"]), "min_hole_clearance": r.get("hole_clearance", 0.25),
                  "min_copper_edge_clearance": r["edge_clearance"]})
    base = {"clearance": r["clearance"], "via_diameter": r["via"], "via_drill": r["drill"], "microvia_diameter": 0.3,
            "microvia_drill": 0.1, "diff_pair_width": 0.2, "diff_pair_gap": 0.25, "wire_width": 6, "bus_width": 12, "line_style": 0,
            "pcb_color": "rgba(0, 0, 0, 0.000)", "schematic_color": "rgba(0, 0, 0, 0.000)", "priority": 2147483647}
    classes = [dict(base, name="Default", track_width=r["track"])]
    patterns = []
    for i, (netname, width) in enumerate(sorted(spec.get("power_nets", {}).items())):
        cname = f"Power {width} mm"
        if not any(c["name"] == cname for c in classes):
            classes.append(dict(base, name=cname, track_width=width, via_diameter=max(r["via"], 0.8), priority=i))
        patterns.append({"netclass": cname, "pattern": netname})
    pro["net_settings"] = {"classes": classes, "meta": {"version": 4}, "net_colors": None, "netclass_assignments": None,
                           "netclass_patterns": patterns}
    pro.setdefault("meta", {"filename": name + ".kicad_pro", "version": 3})
    json.dump(pro, open(pro_path, "w"), indent=2)


def dsn(d):
    name, pcb = paths(d)
    board = pcbnew.LoadBoard(pcb)
    ok = pcbnew.ExportSpecctraDSN(board, os.path.join(d, name + ".dsn"))
    print("exported", name + ".dsn" if ok else "DSN export FAILED")


def ses(d):
    name, pcb = paths(d)
    spec = json.load(open(os.path.join(d, "board.json")))
    board = pcbnew.LoadBoard(pcb)
    if not pcbnew.ImportSpecctraSES(board, os.path.join(d, name + ".ses")):
        sys.exit("SES import FAILED")
    g = spec.get("ground_pour")
    if g:
        w, h = spec["width"], spec["height"]
        for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
            z = pcbnew.ZONE(board)
            z.SetLayer(layer)
            z.SetNetCode(board.GetNetcodeFromNetname(g))
            z.SetLocalClearance(MM(spec["rules"]["clearance"] + 0.1))
            z.SetMinThickness(MM(0.25))
            z.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL)
            ol = z.Outline()
            ol.NewOutline()
            for x, y in ((0, 0), (w, 0), (w, h), (0, h)):
                ol.Append(P(x, y))
            board.Add(z)
        pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    pcbnew.SaveBoard(pcb, board)
    print("routed board saved with ground pours" if g else "routed board saved")


def tidy(d):
    """Remove vias the router left connected to nothing (DRC lists them as via_dangling), then refill the pours."""
    name, pcb = paths(d)
    board = pcbnew.LoadBoard(pcb)
    report = json.load(open(os.path.join(d, "drc.json")))
    spots = [i["pos"] for v in report["violations"] if v["type"] == "via_dangling" for i in v["items"] if "Via" in i.get("description", "")]
    gone = 0
    for t in list(board.GetTracks()):
        if t.GetClass() == "PCB_VIA":
            x, y = pcbnew.ToMM(t.GetPosition().x), pcbnew.ToMM(t.GetPosition().y)
            if any(abs(x - s["x"]) < 0.01 and abs(y - s["y"]) < 0.01 for s in spots):
                board.Remove(t)
                gone += 1
    # pads the DRC calls starved (a thermal with one spoke, boxed in by tracks): join them to the pour solidly
    starved = {i["description"].split(" of ")[1].split(" on ")[0] + ":" + i["description"].split()[1]
               for v in report["violations"] if v["type"] == "starved_thermal" for i in v["items"] if i.get("description", "").startswith("Pad")}
    fixed = 0
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            if f"{fp.GetReference()}:{pad.GetNumber()}" in starved:
                pad.SetLocalZoneConnection(pcbnew.ZONE_CONNECTION_FULL)
                fixed += 1
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    pcbnew.SaveBoard(pcb, board)
    print(f"removed {gone} dangling vias; {fixed} starved pads joined to the pour solidly")


if __name__ == "__main__":
    {"new": new, "dsn": dsn, "ses": ses, "tidy": tidy}[sys.argv[1]](os.path.abspath(sys.argv[2]))
