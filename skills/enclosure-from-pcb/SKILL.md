---
name: enclosure-from-pcb
description: 'Designs a 3D printed case around a real circuit board, from its KiCad files or measured dimensions: standoffs on the mounting holes, openings for every connector and button, light pipes or a diffuser for LEDs, vents, a lid that clips or screws, printed-in labels, and a preview of the board inside the case on the Print Bench. Use when someone says "make a case for this board", "enclosure for my PCB", "box for my ESP32 / Pi / Arduino", "design a housing", or after pcb-layout finishes a board.'
---

# Enclosure from a PCB

Tools: `tools/` two folders above this skill's base directory (the plugin root, or the repo root in a clone).

The case is built from the board's own numbers, so they cannot drift apart: outline, hole positions, connector positions and the tallest part come from the KiCad board (`pcb.py dims hardware/<name>` writes them to `hardware/<name>/board_dims.scad`) or, for a bought board, from the person's caliper numbers and the maker's drawing.

## Design rules

- **Clearance:** 0.5 mm round the board edge, 1 mm above the tallest part, 2 mm under the board for solder joints and pins.
- **Standoffs** on every mounting hole: 6 mm outer diameter, a 2.5 mm pilot for an M3 self-tapping screw (or 4.0 mm pockets for M3 heat-set inserts), height from the under-board clearance.
- **Openings** for each connector: the connector's real size plus 0.5 mm each side, centred on its position in board.json, with a 0.5 mm chamfer. USB-C needs room for the plug's overmould: at least 12.5 x 7 mm at the outer face.
- **Buttons:** a printed plunger over each switch, or a flexure tab cut into the lid.
- **LEDs:** a diffuser window printed in white or natural PETG (0.6 to 1.2 mm thick reads evenly), or light pipes over single LEDs.
- **Heat:** vents (slots 1.5 mm wide) over regulators and radio modules. PETG for anything that runs warm; PLA softens around 55 C.
- **Lid:** snap-fit lips (0.3 mm interference) for small cases, M3 screws into the standoffs for anything opened often.
- **Labels and port names** printed in, never stickers. Name the ports the way SPECS.md does.
- Every size a named variable; the board's numbers are read from board.json, never typed in.

## Steps

1. Get board.json (from KiCad, or write it from measurements and mark those values FIT).
2. Write `hardware/<name>/case.scad` with parts `base`, `lid`, `labels` (and `diffuser` if it has LEDs).
3. Export and audit with the print-workflow tools (`export_parts.py`, `mesh_audit.py`).
4. Add a bench entry with the board itself as a part (`pcb.py fab` writes `board.stl`) so the page shows the board sitting in the case, and check a clearance with Measure: board edge to wall, top of the tallest part to the lid.
5. Hand over: print orientation for each part, screws or inserts needed, and the order to assemble it.
