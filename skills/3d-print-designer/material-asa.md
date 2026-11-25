# ASA Material Reference for OpenSCAD Design

**Print settings (for the header):** nozzle 240-260C, bed 90-110C, fan 0-30% (a short burst on bridges), 5-10mm brim. Dry at 75-80C for 4-6h.

**Enclosure and ventilation (put both in the PRINT SETTINGS notes):** ASA prints like ABS. An enclosed printer is strongly recommended for anything over about 80mm, and ventilation is mandatory: ASA gives off styrene and other volatile compounds and ultrafine particles while printing. Vent outdoors or through a HEPA and activated-carbon filter (`activate_air_filtration` where the printer has one). Never print it regularly in a room people sleep in.

## Properties

| Property | ASA | vs ABS / PETG |
|---|---|---|
| Tensile strength | 35-50 MPa (bulk) | Similar / slightly lower |
| Elongation at break | 10-40% | Similar / less |
| Impact (Izod, notched) | 8-20 kJ/m² | Similar / better |
| Tg / HDT (0.45 MPa) | 95-105C / 85-95C | Similar / +20C |
| UV resistance | Excellent: colour and strength hold for years outdoors | Much better / much better |
| Shrinkage | 0.4-0.7% | Slightly less / more |
| Density | 1.07 g/cm³ | Similar / lighter |

Figures are typical datasheet ranges (Polymaker ASA, Bambu Lab ASA, Prusament ASA).

## When to choose it

ASA is ABS with UV resistance. Choose it for **outdoor parts in sun** (garden fittings, car exterior trim, mounts on a wall facing south), and for parts near heat (up to about 85C) that also see light. Indoors and out of the sun, PETG is easier and usually enough.

## Design Rules

ASA follows [material-abs.md](material-abs.md) for walls, overhangs, fillets and orientation:

- **Fillet every internal corner** (0.5mm minimum, 1-2mm preferred): like ABS it is notch-sensitive.
- **Warping is the main risk.** Round footprint corners (5-10mm radius), chamfer bottom edges, mouse ears on sharp corners, and leave room for a brim. Scale precise fits by about 1.005 for the shrinkage.
- **Orientation:** keep the main load along the layers (XY), never pulling layers apart.
- **Clearances:** sliding 0.3-0.4mm, snap 0.2mm, press -0.05 to -0.10mm (start points: confirm with the fit card).
- **Outdoor walls:** 3-4 perimeters; water gets into layer lines, so print water-facing faces vertical and avoid flat tops that hold water.

## Failure Modes

- **Warping and cracking between layers:** draughts and a cold enclosure. Keep the door shut, the fan low, and do not open the enclosure until the part is near room temperature.
- **Brittle at sharp corners:** fillets.
- **Smell and fumes:** ventilation, as above.

ASA can be acetone-smoothed and acetone-welded like ABS, more slowly ([print-postprocess](../print-postprocess/SKILL.md)).

## Sources

Polymaker ASA, Bambu Lab ASA and Prusament ASA technical datasheets, Prusa Knowledge Base (ASA), Bambu Lab wiki (filament drying, enclosure ventilation). Researched November 2026; numbers are typical ranges, not one product's.
