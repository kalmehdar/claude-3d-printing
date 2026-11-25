# Nylon (PA) Material Reference for OpenSCAD Design

**Print settings (for the header):** nozzle 250-290C depending on grade (PA12 and "low-temperature" PA lower, PA6 and PA66 higher: the spool's label wins), bed 70-100C, fan 0-30%, enclosure recommended. **Print it dry or not at all:** dry at 70-90C for 8-12h, then print straight from a dry box. Nylon pulls in moisture within hours in a damp room.

**Plate:** glue stick (or the maker's adhesive) on textured or smooth PEI, both for grip and as a release layer. Some nylons barely stick to bare PEI; others bond hard enough to damage it.

## Properties

| Property | PA (unfilled, dry) | vs PETG / ABS |
|---|---|---|
| Tensile strength | 40-80 MPa (bulk; varies by grade) | Higher |
| Elongation at break | 50-300% | Similar to PETG / far more than ABS |
| Impact (Izod, notched) | High | Better than both |
| Tg / HDT (0.45 MPa) | 50-70C (Tg) / 80-180C by grade | HDT higher |
| Friction and wear | Low friction, excellent wear | Much better: the best choice for gears and bushings |
| Moisture uptake | 1-3% at room humidity (PA6 most, PA12 least) | Much more |
| Shrinkage | 0.7-2% | More |

Figures are typical datasheet ranges (Polymaker PolyMide, Bambu Lab PA6 and PAHT, Prusament PA11). Nylon's properties move with moisture: dry, it is stiffer and more brittle; after it absorbs water from the air it gets tougher and more flexible, and slightly larger.

## When to choose it

Gears, bushings, sliding parts, hinges and clips that flex many times, tough brackets and anything that must survive being dropped or worn. Not for parts that must hold an exact size in changing humidity, or that sit in water (except PA12, which absorbs least).

## Design Rules

- **Clearances:** nylon swells as it takes on moisture: sliding 0.4-0.5mm, snap 0.3mm, press 0 to -0.05mm. Check with the fit card after the part has conditioned for a day or two.
- **Walls:** 1.6mm minimum (4 perimeters) for functional parts; nylon is strong per wall, so walls beat infill.
- **Warping:** worse than PETG. Round corners, brim, enclosure. Long flat parts curl: split them or add ribs.
- **Overhangs:** about 45°. Supports bond firmly; design support-free where possible.
- **Gears and bearings:** print flat, 100% infill in the teeth, and let parts condition before final fitting.
- **Heat-set inserts:** find the hole with the insert card in nylon itself; do not reuse a PLA or PETG number.

## Failure Modes

- **Wet filament:** popping, steam, rough bubbled surface, stringing and weak layers. Dry it; this is the cause far more often than settings.
- **Warping and corner lift:** enclosure, glue, brim, slower first layer.
- **Size drift after printing:** expected; design clearances for the conditioned part.

## Sources

Polymaker PolyMide and Bambu Lab PA6/PAHT technical datasheets, Prusa Knowledge Base (nylon and PA11), Bambu Lab wiki (filament drying, PA printing guide). Researched November 2026; numbers are typical ranges, not one product's.
