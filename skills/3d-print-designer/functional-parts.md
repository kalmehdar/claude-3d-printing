# Functional parts: inserts, screws, snaps, hinges, magnets, Gridfinity

Numbers for parts that hold, join or move. Each row says where it came from: **[sourced]** was read from the named source; **[start]** is a common starting point that no source here confirmed, so print a small test coupon before trusting it. The printer's own measured fits (`python tools/fit.py show`) beat every number here for clearances. Researched November 2026; manufacturers' own tables were often images, so verify against the insert or screw you actually bought.

## Heat-set threaded inserts (brass, knurled)

| What | Value | Status |
| --- | --- | --- |
| Hole shape | Straight, not tapered, no chamfer | [sourced] CNC Kitchen |
| Hole size, how to choose | Just big enough that the insert presses in without a burr under it; print holes small to large and take the first with no burr | [sourced] CNC Kitchen, Prusa |
| M3 x 5.7 mm (the common one) | Test 4.0 to 4.4 mm; start at 4.0 to 4.2 | [sourced] CNC Kitchen test range |
| M2, M2.5, M4, M5 | About 3.2, 3.6 to 3.8, 5.6, 6.4 mm | [start] coupon first |
| Insert lengths | M3 5.7 (short 3); M4 8.1 (short 4); M5 9.5 (short 5.8) | [sourced] Prusa (CNC Kitchen inserts) |
| Blind hole depth | Insert length + about 1 mm | [sourced] CNC Kitchen |
| Wall around it | At least 1.6 mm; better, half the insert's outside diameter | [sourced, weak] Hubs, FreeCAD tutorial |
| Iron temperature | Print temperature + 10 to 20 °C: PLA about 225, PETG about 245, ABS about 265 °C | [sourced] CNC Kitchen, Prusa |
| Fitting | Melt about 90% in with the iron, press flush with a flat tool, hold a few seconds | [sourced] CNC Kitchen |

Make the hole a named variable (`insert_d = 4.1; // FIT: insert test coupon`), never a literal.

## Screws

| What | Value | Status |
| --- | --- | --- |
| Clearance hole, printed part, M3 | About 3.4 to 3.6 mm (ISO close/medium 3.2/3.4 is for machined holes; printed holes shrink) | [sourced] OpenAstroTech; ISO values [start] |
| Clearance, M2 / M2.5 / M4 / M5 | Machined close/medium 2.2/2.4, 2.7/2.9, 4.3/4.5, 5.3/5.5, plus about 0.2 for print shrink | [start] ISO 273 from memory |
| Self-tapping pilot | Screw diameter minus 0.4 mm (M3 about 2.6 to 2.9), boss at least 6 mm across for M3 | [sourced] rigid-prints, OpenAstroTech |
| Engagement | About 5 mm for M3 and M4 | [sourced] Bambu forum |
| Taken apart often | Use a heat-set insert, not a self-tapper | [sourced] Hubs |
| Socket head (ISO 4762), head diameter / height | M2 3.8 / 2; M2.5 4.5 / 2.5; M3 5.5 / 3; M4 7 / 4; M5 8.5 / 5 | [sourced] Misumi, ISO 4762 table (sources differ by up to 0.2) |
| Counterbore | Head diameter + 0.5, head height + 0.2 deep | [start] |
| Countersunk head (ISO 10642), 90° | M3 about 6.7, M4 about 9, M5 about 11.2 across | [start] |
| Hex nut across flats (ISO 4032) | M3 5.5, M4 7.0; trap = across flats + 0.2 to 0.4; across corners = flats / cos 30° | nut sizes [start]; trap clearance [sourced] Hubs |

Horizontal holes: a teardrop top (BOSL2 `teardrop`) prints without supports.

## Printed threads

- Avoid printed threads below M5; M8 and up with a pitch of 1.25 or more prints reliably. [sourced] Hubs (the M8 figure is [start])
- Libraries: **BOSL2** `threading.scad` (`threaded_rod`, `threaded_nut`, trapezoidal, ACME, NPT; `$slop` for printer clearance; blunt starts by default; BSD 2-Clause) and **threadlib** (metric, UNC/UNF, BSP, bottle threads; BSD 3-Clause). [sourced] their repos
- Prefer an insert or a nut trap for anything under M8.

## Snap fits (cantilever)

- Strain at the root: `e = 1.5 · t · y / L²` (t thickness at the root, y deflection, L length; rigid wall). Deflection allowed: `y = 0.67 · e · L² / t`. [sourced] Bayer-derived snap-fit guide via core77
- Design strain: about 1.5 to 2% for PLA, about 3% for PETG (PLA is brittle; published PLA limits of 4 to 8% are too high for repeated use). [start]
- Print the arm flat in X and Y; an arm bending across layers takes about half the strain. [sourced] core77
- Lead angle about 30°; a return angle equal to the lead gives equal on and off force, 90° makes it permanent. Taper the arm to spread strain. [sourced concept] core77; angles [start]

## Hinges and fits

| What | Value | Status |
| --- | --- | --- |
| Living hinge material | PP, nylon or TPE for anything that flexes often; PLA is unsuitable | [sourced] MatterHackers |
| PETG living hinge | Folding test: PETG about 850 cycles, PLA about 160 to 200 | [sourced] UT Austin paper |
| Living hinge thickness | 0.3 to 0.8 mm, at least 5 mm long | [sourced] Hubs |
| Print-in-place clearance | 0.3 mm to start, 0.25 on a well-tuned printer; 0.15 and below fuses | [sourced, forum] Prusa forum, Snapmaker blog |
| Printed pin-and-barrel hinge | `use <examples/models/pip-hinge.scad>` then `hinge(pip_gap)`: gap all round the pin (never under 0.3, so more than one layer under it), 45 degree teardrop roof on the hole, axis along the bed, leaves flat. `pip_gap` comes from `tools/fit.py scad` once the hinge ladder (same file) is printed | [start] measure with the ladder |
| Press fit / slide fit | From the fit card (`tools/fit.py`); without it, press 0 to +0.1 per side, slide +0.2 to 0.3 per side | [start] |

## Magnets (disc, press fit)

| What | Value | Status |
| --- | --- | --- |
| Pocket | Diameter + 0.2 mm (PLA), + 0.3 mm (PETG); thickness + 0.05 to 0.25 | [sourced, single user] MakerWorld |
| 10 x 2 mm | Worked at 10.2 x 2.05 | [sourced, single user] |
| 6 x 3 / 6 x 2 | Start at 6.1 to 6.2; Gridfinity's own pocket is 6.5 x 2.4 (glued) | [start]; Gridfinity [sourced] |
| Test bar | Holes 6.05 to 6.40 in 0.05 steps; PETG differs from PLA | [sourced] MakerWorld |
| Glued | Pocket + 0.3 to 0.4 with CA glue, or pause and print over it | [start] |

## Gridfinity

From kennetek/gridfinity-rebuilt-openscad `src/core/standard.scad` (MIT), Zack Freedman's original (MIT, 2022).

| What | Value |
| --- | --- |
| Grid | 42 x 42 mm; a bin is cells x 42 - 0.5 |
| Height unit | 7 mm |
| Base | 41.5 square at the top, 35.6 at the bottom; profile (0,0) (0.8,0.8) (0.8,2.6) (2.95,4.75), 4.75 high; corner radius 3.75 top, 0.8 bottom |
| Stacking lip | (0,0) (0.7,0.7) (0.7,2.5) (2.6,4.5) |
| Magnet / screw holes | Magnet 6.5 x 2.4; screw 3 mm; 8 mm from the side |
| Walls | Minimum 0.95; divider 1.2; inside fillet 2.8 |

Building on that library (MIT) is better than re-deriving the profile; credit it.

## Sources

CNC Kitchen (heat-set insert tips and test parts), Prusa (insert product pages), McMaster 94459A140, Hubs (threaded fasteners in printed parts), core77 (snap-fit design), BOSL2 and threadlib repos, kennetek/gridfinity-rebuilt-openscad, MatterHackers (living hinges), UT Austin Balderrama-Armendariz 2016 (hinge cycles), Misumi (ISO 4762), OpenAstroTech and rigid-prints design guides, Prusa and Bambu forums (clearances).
