---
name: pcb-layout
description: 'Turns a reviewed circuit (SKiDL circuit.py from electronics-design) into a KiCad PCB: board outline and mounting holes from the spec, placement, autorouting with Freerouting, design-rule checks, then Gerbers, drill files, BOM and pick-and-place for ordering, a 3D board model and pictures. Use when someone says "lay out the PCB", "make the board", "route it", "get Gerbers", "order this from JLCPCB/PCBWay", "make a PCB for this circuit", or "show me the board in 3D".'
---

# PCB layout

Tools: `tools/` two folders above this skill's base directory (the plugin root, or the repo root in a clone).

Needs KiCad 10 (`kicad-cli`, and KiCad's own Python for the board steps) and Java for Freerouting. `tools/pcb.py` runs every step; run it with no arguments to list them. Read SPECS.md and circuit.py before starting.

## Rules Claude follows on every board

- **Board house limits first.** Default rules (safe for JLCPCB and PCBWay standard 2-layer): 0.2 mm trace and space, 0.3 mm drill, 0.6 mm via pad, 0.5 mm copper to board edge, 1.6 mm FR-4, 1 oz copper. Tighter only when the person names a house and its capability.
- **Trace width from current, not by eye.** `python tools/trace_width.py <amps>` for every power net, with the worst case from the power budget; round up. Signal nets 0.2 to 0.25 mm.
- **Ground:** a ground pour on both layers, stitched with vias around the board and next to every IC's ground pin. Nothing routes through the pour under a USB or RF part except its own pins.
- **Placement before routing:** connectors on the edges the spec names, facing out. Decoupling capacitors within 2 mm of their IC's power pin. Regulators near the input, then the loads. Radio modules (ESP32, nRF) at a board edge with the antenna over no copper on any layer, per the module's datasheet keep-out.
- **Mounting holes** from SPECS.md (M3: 3.2 mm hole, 6 mm keep-out), never moved without changing the spec, because the enclosure is built from the same numbers.
- **Silkscreen** carries the board name, revision, every connector's pinout, and polarity marks. Text at least 0.8 mm high.

## Steps

A project lives in `hardware/<name>/` with `SPECS.md`, `circuit.py` (from electronics-design) and `layout.py`. `hardware/led-bar` is the worked example: copy its shape.

1. **Layout:** write `layout.py`: board size, rules, power track widths (from `tools/trace_width.py`), the ground-pour net, the tallest part, and a position and rotation for every part, following the rules above. It writes `board.json`.
2. **Build and look:** `python tools/pcb.py build hardware/<name>`, then `kicad-cli pcb render` (or `pcb.py fab`) and look at the placement before routing: connectors at the edges, antenna keep-out clear, decoupling next to its pins.
3. **Route:** `python tools/pcb.py route hardware/<name>` exports to Freerouting, routes, imports the result and pours ground on both layers.
4. **Check:** `python tools/pcb.py check hardware/<name>` runs KiCad's DRC, removes dangling vias, joins starved thermal pads solidly, and must end `0 errors, 0 unconnected`. Read every warning too. If routing squeezes a track below the minimum, move parts to give it room rather than lowering the rule.
5. **Fab:** `python tools/pcb.py fab hardware/<name>` writes `fab/`: the Gerbers zip, JLCPCB BOM and placement files, STEP, STL and renders. Every BOM line needs an LCSC number checked on JLCPCB's listing; the step lists any that are missing.
6. **For the case:** `python tools/pcb.py dims hardware/<name>` writes `board_dims.scad` for enclosure-from-pcb.

`python tools/pcb.py all hardware/<name>` runs the circuit check and steps 2 to 6 in order.

Lessons from the first board, built into the tools: KiCad 7 and later keep net classes in the `.kicad_pro` file (the builder writes it, or the router ignores the power widths); SKiDL's no-connect net `__NOCONNECT` is not a real net; library footprints sometimes need their own clearances (record those in REVIEW.md, never hide them).

## Before anything is ordered (the gate)

An autorouted board from Claude is a first pass. Before the person pays a board house:

- They open it in KiCad and look at every power trace and the USB and antenna areas.
- The checklist in `hardware/<name>/REVIEW.md` is ticked by a person: footprints match the parts on the BOM (pin 1, polarity, package size), connectors face out, holes match the case, the power budget fits the traces and the supply.
- An independent review (a fresh agent given the files, not your conclusion) finds no P0 or P1 problem.

Claude never places an order or uploads to a board house: the person does, after the gate.
