# TPU Material Reference for OpenSCAD Design

**Print settings (for the header):** nozzle 220-235C (the spool's label wins), bed 30-60C, fan 50-100% (off for the first layer), no enclosure. Print slowly: max volumetric speed about 3-6 mm³/s for 95A (`filament_max_volumetric_speed`), retraction short or off (0-1mm direct drive, `filament_retraction_length`). Hygroscopic: dry at 50-55C for 4-8h; wet TPU strings and bubbles badly.

**Can the printer feed it?** TPU buckles in a long or loose filament path. Direct drive handles 95A; 85A and softer needs a tight, constrained path and slow speeds; Bowden printers struggle with anything under 95A. Check the printer maker's guidance first (on the Snapmaker U1 the printer marks soft filaments with `filament_soft`; see [docs/snapmaker-u1.md](../../docs/snapmaker-u1.md)). Most multi-head and AMS-style feeders do not take TPU: print it from a head fed straight from the spool.

## Properties

| Property | TPU 95A (typical) | vs PLA / PETG |
|---|---|---|
| Shore hardness | 95A (common), 85A, 90A, 64D | Rubbery, not rigid |
| Elongation at break | 300-600% | 50-100x PLA |
| Tensile strength | 25-45 MPa (bulk) | Lower, but it stretches instead of snapping |
| Tg | well below 0C | Stays flexible in a freezer |
| Abrasion resistance | Excellent | Much better |
| Layer adhesion | Very good at the right temperature | Better than PLA |
| Density | 1.20-1.23 g/cm³ | Similar |

Figures are typical datasheet ranges (Polymaker, Bambu Lab, Prusament); the spool's own datasheet wins.

## Design Rules

**Stiffness comes from geometry and infill, not the plastic.** The same TPU part is soft at 2 walls and 10% gyroid and nearly rigid at 5 walls and 50%. Say which feel the part needs in PRINT SETTINGS: walls (`wall_loops`) and infill set it.

**Walls:** 1.2mm minimum (3 perimeters); thinner walls print, but wobble and string. A 0.8mm single-wall skin makes a soft membrane.

**Overhangs and bridges:** about as good as PETG; keep overhangs to 45-50° and bridges short. Supports fuse to TPU and are hard to remove: avoid them, or use a PLA interface (see [print-postprocess](../print-postprocess/SKILL.md)).

**Clearances:** TPU parts squeeze in, so press fits can be tighter than rigid plastic: start at 0 to -0.1mm and print the fit card ([examples/models/fit-card.scad](../../examples/models/fit-card.scad)) before trusting a number. Sliding fits need more room (0.4-0.5mm): TPU grips.

**Uses that suit it:** gaskets and seals, feet and bumpers, phone cases, grips and handles, cable strain reliefs, belts, living hinges that flex millions of times, vibration dampers. **Avoid:** threads that must hold torque, precise sliding parts, anything that must hold a shape under load.

**Multi-material:** TPU bonds well to PLA and PETG as a soft-touch skin or overmould in one print (grippy feet on a rigid part). It does not bond to itself through a support interface cleanly; plan the soft and hard regions as separate parts in the design.

## Failure Modes

- **Feed failure:** the filament buckles in the extruder and the print under-extrudes or stops. Slower, short retraction, constrained path.
- **Stringing and blobs:** dry the spool first, then lower temperature 5C at a time.
- **Too strong a bond to smooth PEI:** TPU can tear the PEI film off a smooth plate. Use a textured plate, or glue stick as a release layer.
- **Shrinking holes:** small holes close up; enlarge by 0.2-0.3mm or drill after.

## Sources

Polymaker PolyFlex TPU95 and Bambu Lab TPU 95A HF technical datasheets, Prusa Knowledge Base (flexible materials), Bambu Lab wiki (TPU printing guide, filament drying), OrcaSlicer wiki (setting keys). Researched November 2026; numbers are typical ranges, not one product's.
