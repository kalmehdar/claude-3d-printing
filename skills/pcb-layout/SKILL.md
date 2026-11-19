---
name: pcb-layout
description: 'Turns a reviewed circuit (SKiDL circuit.py from electronics-design) into a KiCad PCB: board outline and mounting holes from the spec, placement, autorouting with Freerouting, design-rule checks, then Gerbers, drill files, BOM and pick-and-place for ordering, a 3D board model and pictures. Use when someone says "lay out the PCB", "make the board", "route it", "get Gerbers", "order this from JLCPCB/PCBWay", "make a PCB for this circuit", or "show me the board in 3D".'
---

# PCB layout

Needs KiCad 10 (`kicad-cli`, and KiCad's own Python for the board steps) and Java for Freerouting. `tools/pcb.py` runs every step; `python tools/pcb.py --help` lists them. Read SPECS.md and circuit.py before starting.

## Rules Claude follows on every board

- **Board house limits first.** Default rules (safe for JLCPCB and PCBWay standard 2-layer): 0.2 mm trace and space, 0.3 mm drill, 0.6 mm via pad, 0.5 mm copper to board edge, 1.6 mm FR-4, 1 oz copper. Tighter only when the person names a house and its capability.
- **Trace width from current, not by eye.** `python tools/trace_width.py <amps>` for every power net, with the worst case from the power budget; round up. Signal nets 0.2 to 0.25 mm.
- **Ground:** a ground pour on both layers, stitched with vias around the board and next to every IC's ground pin. Nothing routes through the pour under a USB or RF part except its own pins.
- **Placement before routing:** connectors on the edges the spec names, facing out. Decoupling capacitors within 2 mm of their IC's power pin. Regulators near the input, then the loads. Radio modules (ESP32, nRF) at a board edge with the antenna over no copper on any layer, per the module's datasheet keep-out.
- **Mounting holes** from SPECS.md (M3: 3.2 mm hole, 6 mm keep-out), never moved without changing the spec, because the enclosure is built from the same numbers.
- **Silkscreen** carries the board name, revision, every connector's pinout, and polarity marks. Text at least 0.8 mm high.

## Steps

1. `pcb.py new hardware/<name>` makes the KiCad project from circuit.py's netlist with every footprint, and draws the outline and holes from SPECS.md.
2. Place parts: by script from a placement table in `hardware/<name>/placement.json` (reference, x, y, rotation, side), following the rules above. Show the person a render of the placement before routing.
3. `pcb.py route hardware/<name>` exports the board to Freerouting, routes it, and imports the result. Then add the ground pours.
4. `pcb.py check hardware/<name>`: ERC on the schematic and DRC on the board, both at zero errors. Unconnected nets count as errors. Fix and re-run; never waive a check without saying so to the person.
5. `pcb.py fab hardware/<name>`: Gerbers, drill, BOM with LCSC numbers, pick-and-place, all zipped for upload, plus a STEP model and top and bottom renders.

## Before anything is ordered (the gate)

An autorouted board from Claude is a first pass. Before the person pays a board house:

- They open it in KiCad and look at every power trace and the USB and antenna areas.
- The checklist in `hardware/<name>/REVIEW.md` is ticked by a person: footprints match the parts on the BOM (pin 1, polarity, package size), connectors face out, holes match the case, the power budget fits the traces and the supply.
- An independent review (a fresh agent given the files, not your conclusion) finds no P0 or P1 problem.

Claude never places an order or uploads to a board house: the person does, after the gate.
