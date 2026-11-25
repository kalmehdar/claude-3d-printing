# Carbon-Fibre-Filled Material Reference for OpenSCAD Design

Carbon-fibre (CF) filaments are a base plastic (PLA, PETG, ABS, ASA, PA, PC) filled with short chopped fibres, usually 10-20% by weight. **Read the base material's file too** ([material-pla.md](material-pla.md), [material-petg.md](material-petg.md), [material-abs.md](material-abs.md), [material-asa.md](material-asa.md), [material-nylon.md](material-nylon.md)): temperatures, plate and enclosure come from the base. This file covers what the fibre changes. Glass-fibre (GF) fills behave the same way.

**Print settings (for the header):** the base material's temperatures, often 5-10C hotter (the spool's label wins). **Hardened steel (or other wear-resistant) nozzle, mandatory**: the fibres are abrasive and wear a brass nozzle out quickly, after which it under-extrudes and the bore widens. 0.4mm works; 0.6mm clogs less and is the common recommendation. Dry it like the base material (nylon-CF especially).

## What the fibre changes

| Property | vs the unfilled base |
|---|---|
| Stiffness (modulus) | Much higher, often 1.5-3x |
| Strength along the layers | Similar or slightly higher |
| Layer adhesion (Z strength) | Lower: fibres do not cross layers |
| Impact toughness | Lower: more brittle |
| Warping and shrinkage | Lower: fibres hold the shape |
| Surface | Matte, hides layer lines |
| Stringing | Usually less |
| Dimensional stability | Better |

These are general trends from the makers' datasheets; how much each changes depends on the base and the fibre content.

## When to choose it

Stiff brackets, frames, jigs and fixtures, drone and RC parts, camera mounts: anything that must not flex and is loaded along its layers. **Not** for parts loaded across the layers, snap-fits and living hinges (too brittle), or anything that must take an impact.

## Design Rules

- **Orient for layer strength first**: Z strength is lower than the base material, so keep tension and bending in XY even more strictly.
- **Snap-fits:** halve the base material's allowable strain, or use a screw instead.
- **Walls:** more perimeters (4+) rather than more infill; the stiffness is in the walls.
- **Small details and text** lose crispness through a 0.6mm nozzle; keep text 1mm stroke or bigger.
- **Electronics:** carbon-filled parts can be slightly conductive. Do not rely on them to insulate live parts.

## Handling

- **Sanding and cutting make fine carbon dust.** Sand wet or with a dust mask, outdoors or with extraction.
- Fibre-filled spools wear feeder gears and PTFE tubes faster too; inspect them more often ([print-maintenance](../print-maintenance/SKILL.md)).

## Sources

Polymaker, Bambu Lab and Prusament CF and GF technical datasheets (PLA-CF, PETG-CF, PA-CF), Bambu Lab wiki (abrasive filaments and hardened nozzles), Prusa Knowledge Base (abrasive materials). Researched November 2026; numbers are general trends, not one product's.
