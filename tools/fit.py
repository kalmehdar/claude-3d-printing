"""Your printer's measured fits, kept in one place so every design uses them instead of a material guess.

    python fit.py show                                   the stored fits, or the defaults if none
    python fit.py set --slide 0.2 --press 0.1 [--printer "Bambu P1S, PETG"]
    python fit.py scad                                   OpenSCAD lines to paste at the top of a design

Measure them with examples/models/fit-card.scad: print the card and the peg, try the peg in each hole.
  slide  smallest hole (diameter clearance, mm) the peg turns in freely
  press  smallest hole the peg goes into with a firm push and stays
Stored in ~/.config/claude-3d-printing/fit.json (override with FIT_FILE). Standard library only.
"""
import argparse
import json
import os
import sys

PATH = os.environ.get("FIT_FILE") or os.path.join(os.path.expanduser("~"), ".config", "claude-3d-printing", "fit.json")
DEFAULT = {"slide": 0.3, "press": 0.15, "printer": "not measured: material defaults (PETG)", "measured": False}


def load():
    try:
        return {**DEFAULT, **json.load(open(PATH, encoding="utf-8")), "measured": True}
    except (OSError, ValueError):
        return dict(DEFAULT)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show")
    sub.add_parser("scad")
    s = sub.add_parser("set")
    s.add_argument("--slide", type=float, required=True)
    s.add_argument("--press", type=float, required=True)
    s.add_argument("--printer", default="")
    a = ap.parse_args()
    if a.cmd == "set":
        if not 0 <= a.press <= a.slide <= 1:
            sys.exit("expected 0 <= press <= slide <= 1 mm (a press fit is tighter than a slide fit)")
        os.makedirs(os.path.dirname(PATH), exist_ok=True)
        json.dump({"slide": a.slide, "press": a.press, "printer": a.printer}, open(PATH, "w", encoding="utf-8"), indent=1)
        print(f"stored in {PATH}")
    f = load()
    if a.cmd == "scad":
        print(f"slide_fit = {f['slide']};   // diameter clearance for parts that move ({f['printer']})")
        print(f"press_fit = {f['press']};   // diameter clearance for parts that stay pressed in")
    else:
        print(f"slide {f['slide']} mm, press {f['press']} mm: {f['printer']}" + ("" if f["measured"] else " (print examples/models/fit-card.scad to measure)"))


if __name__ == "__main__":
    main()
