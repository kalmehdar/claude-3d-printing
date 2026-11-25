---
name: print-postprocess
description: 'Finishes a printed part: removing supports (including PLA contact layers under PETG), sanding, filling, priming and painting, annealing, smoothing, gluing and setting heat-set inserts. Use when someone says "how do I get the supports off", "sand this", "paint it", "make it smooth", "anneal", "glue these parts", "put inserts in", or asks how to finish a print.'
---

# Print post-processing

The loop: **plan the finish at design time, then remove, smooth, join, finish.** Most finishing pain is decided in the slicer: a support interface that peels, a seam on a hidden side, an orientation that puts layer lines where nobody looks. Say in the design's PRINT SETTINGS which faces will be finished.

Safety first: cut away from your hands, sand wet or with a dust mask (carbon-filled filaments especially), paint and use solvents with ventilation and gloves, and read the label on any glue or solvent.

## 1. Removing supports

- **Let it cool** fully, then remove supports with flush cutters and pliers, working from the outside in. Tree supports usually snap off by their trunks.
- **Set the gap so supports peel.** The top Z distance (`support_top_z_distance`) and interface layers (`support_interface_top_layers`) decide it. Too small and the support welds on; too big and the underside droops. Check the names with `python3 tools/orca.py settings support --printer <setup>`.
- **PLA contact layers under PETG (and the other way).** PLA and PETG barely bond, so a support interface printed in the other material peels off cleanly and leaves a smooth underside. On a multi-head printer set the support interface to the other head (`support_interface_filament`), keep the support body in the part's own material (`support_filament`) if you want to save the second material, and set the top Z distance to 0 for that interface. Expect a little extra purge for the swaps. Works for PETG on PLA and PLA on PETG; not for ABS or ASA with PLA (they bond better).
- **Soluble supports** (PVA, BVOH for PLA; HIPS in limonene for ABS): soak in warm water (PVA, BVOH) until gone, change the water, dry the part. Keep these filaments sealed (see [filament-care](../filament-care/SKILL.md)).
- **Clean-up:** a deburring tool for edges and holes, a craft knife scraped (not cut) along a scarred surface.

## 2. Sanding and filling

- Start at the grit that removes the defect (often 120-220), work up in steps (220, 400, 600, 800+) and sand **wet** from 400 up: it keeps PLA from melting into the paper and keeps dust down.
- Sand along the layer lines, light pressure: friction heat softens PLA.
- **Fill** layer lines and gaps with a filler primer (spray, several light coats, sanding between) or a two-part filler putty for large gaps. Thin coats beat one thick one.
- PETG and TPU sand poorly (they smear); choose orientation and a good surface over sanding for them.

## 3. Priming and painting

1. Clean off dust and finger grease (soap and water or isopropyl alcohol; check the alcohol does not haze your plastic on a hidden spot first).
2. **Prime** with a plastic or filler primer. It shows the remaining defects: sand, re-prime.
3. **Paint** with acrylics (brush or airbrush) or spray paint for plastics, in thin coats.
4. **Seal** with a clear coat (matte, satin or gloss) for wear.

Printing in the final colour, or multi-colour on a multi-head printer, skips most of this.

## 4. Smoothing

- **ABS and ASA: acetone vapour.** In a closed glass or metal container (never near a flame, with ventilation, gloves and eye protection), a small amount of acetone on paper towels around the part, not touching it. Minutes for ABS, longer for ASA. It rounds small details and edges; take the part out before they go. Let it out-gas for a day before handling.
- **PLA:** acetone does nothing useful. Sand, filler primer, or an epoxy coating (a thin brushed coat of 2-part epoxy fills layer lines and gives a gloss; also seals the surface).
- **Ironing** in the slicer (`ironing_type`) smooths flat top faces at print time.

## 5. Annealing (PLA and some PETG)

Heating a part above its glass transition and letting it cool slowly makes PLA stiffer and more heat-resistant, at the cost of shrinking: typically a few percent in X and Y and slight growth in Z, and possible warping. Results vary a lot by brand and part.

- PLA: in an oven at 70-90 °C for 30-60 minutes, then switch off and let it cool in the oven. Check the oven's real temperature with a thermometer.
- Pack small parts in fine salt or plaster to hold the shape.
- **Print a test piece first** and measure it before and after, then scale the real part to compensate. Never anneal a part whose fits have to stay exact without that test.
- "Annealable" or "HT" PLA grades are made for it; their datasheet gives the temperature.

## 6. Gluing and joining

| Material | Works | Notes |
| --- | --- | --- |
| PLA | Cyanoacrylate (superglue), 2-part epoxy | Superglue for small tight joints, epoxy for gaps and strength |
| PETG | Epoxy, some cyanoacrylates (gel) | Hard to glue; rough up the surfaces first |
| ABS, ASA | Acetone welding, ABS slurry (scraps dissolved in acetone), epoxy | Acetone welds approach the part's own strength |
| TPU | Flexible cyanoacrylate, polyurethane adhesives | Rigid glues crack when it flexes |
| Nylon | Epoxy (some specialist nylon glues) | Very hard to glue; a mechanical joint is better |
| Mixed materials | Epoxy | Clean and rough both faces |

Design joins with alignment features (pins, tongue and groove) and a glue gap of about 0.1-0.2 mm, and clamp while the glue cures. A screw joint with inserts is stronger and comes apart again.

## 7. Heat-set inserts

1. **Find the hole for your printer and material first:** print the insert card ([examples/models/insert-card.scad](../../examples/models/insert-card.scad)), set an insert in each size, pick the smallest that sits flush and does not turn, and record it: `python3 tools/fit.py set --slide <mm> --press <mm> --insert <mm>`. Every later design uses it.
2. Design the boss about 2x the hole diameter and the pocket about 1 mm deeper than the insert ([pattern-fasteners.md](../3d-print-designer/pattern-fasteners.md)), with a small chamfer as a lead-in.
3. Use a soldering iron with an insert tip, at the temperature [pattern-fasteners.md](../3d-print-designer/pattern-fasteners.md) gives for the material (or the insert maker's datasheet). Press straight down; stop about 90% in and press the last bit flush with a flat cool tool so the insert ends square.
4. Let it cool before loading the thread. Too hot or too fast and plastic wells up over the insert or it goes in crooked: heat it out and try again, or move to the next card size.

## Sources

Prusa Knowledge Base (post-processing, supports, smoothing), Bambu Lab wiki (support interface with a different material, PLA/PETG interface), OrcaSlicer wiki (support settings), CNC Kitchen (heat-set insert and annealing tests), the insert card in this repo. Researched November 2026.
