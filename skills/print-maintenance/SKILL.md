---
name: print-maintenance
description: 'Keeps an FDM printer printing well: what to clean, check and lubricate and how often (plate, nozzle, belts, rods and lead screws, fans, wiring), how to clean a textured PEI plate safely, and the due list Workshop keeps for Klipper printers. Use when someone says "what maintenance does my printer need", "clean the plate", "change the nozzle", "grease the rods", "belt tension", "it is due", or when prints get worse slowly with no setting change.'
---

# Printer maintenance

The loop: **small and often, by prints and by days, and write down when it was done.** A print that gets slowly worse with no setting change usually wants maintenance, not tuning: a greasy plate, a worn nozzle, a slack belt.

Follow the printer maker's manual where it differs from this page: it knows its own parts (which rods take grease and which must stay dry, which plate coating it has). Power off and let the hotend and bed cool before touching anything inside.

## Intervals

Common intervals; the maker's own wins.

| Part | Every | What to do |
| --- | --- | --- |
| Build plate | 5-10 prints, or when the first layer stops sticking | Wash it (below) |
| Nozzle tip | Each print or so | Wipe ooze off while warm with a brass brush |
| Nozzle | Brass: when extrusion gets inconsistent, holes grow, or after about a kilogram of abrasive filament. Hardened steel: much longer | Replace; check after any crash or clog |
| Belts (X and Y) | Monthly | Check tension (even, firm, no slack; both XY belts alike on a CoreXY), teeth clean and dry |
| Linear rods and rails | Monthly | Wipe clean; a thin film of the lubricant the maker names (often a light oil or lithium grease). **Carbon-fibre rods: wipe dry, never grease** |
| Lead screws (Z) | Quarterly | Clean old grease and dust off, then a little fresh grease |
| Extruder gears | Monthly, more with abrasive filament | Brush out filament dust |
| Fans and heatsinks | Quarterly | Blow out dust; a dusty hotend fan causes heat creep |
| Wiring and connectors | Quarterly | Look for chafing where cables move, loose plugs |
| PTFE tubes | When filament drags or after abrasive filament | Check the ends are square and clean; replace if worn |
| Tool-changer contacts (multi-head printers) | Every few months | Wipe with a barely damp alcohol cloth, per the maker |

## Cleaning a textured PEI plate

1. Let the plate cool below about 40 °C and take it off the printer.
2. Wash it with warm water, a drop of washing-up liquid and a soft brush or the soft side of a sponge. Dish soap removes skin oil and residues that isopropyl alcohol only spreads around.
3. Rinse well, dry with a lint-free towel, and handle it by the edges from then on: a fingerprint is enough to make the next first layer lift.
4. Between washes, wipe with isopropyl alcohol (90%+) when it is just slightly greasy.

**Never use acetone on PEI**: it damages the surface (crazing, loss of grip). No abrasive pads on textured PEI either: they flatten the texture. Smooth PEI tolerates a very fine abrasive pad occasionally to restore grip, if the maker allows it. Glue stick residue washes off with water.

## Nozzle

- **Wear:** a brass nozzle widens with use, much faster with carbon- or glass-filled, glow-in-the-dark and some metallic filaments. Signs: under-extrusion that calibration does not cure, wider lines, poor detail. Use hardened steel for abrasive filaments ([material-cf.md](../3d-print-designer/material-cf.md)).
- **Partial clog:** a cold pull (heat, push some filament through, cool to the material's pull temperature, pull firmly) clears most; a needle from the top for the rest. Follow the printer's own procedure for heads that change automatically.
- After changing a nozzle: re-check Z offset (or let the printer probe), and re-run a quick flow check from [print-calibration](../print-calibration/SKILL.md).

## Workshop keeps the list (Klipper printers)

On a Klipper printer, Workshop tracks the maintenance tasks: each has an interval in prints (the plate, counted from Moonraker's job history) or in days (rods, belts, lead screw, pins, contacts), shows which are due, and records who marked each one done. The tasks and intervals today are the Snapmaker U1's, from its maintenance guide (`MAINT_TASKS` in `tools/workshop.py`); `python3 tools/test_maintenance.py` checks the counting. For another printer, change the list to that printer's manual. When a task comes up due, tell the person what it is and leave the marking done to them after they have done it.

## When to look sooner

- After a crash or a layer shift: belts, pulleys and grub screws.
- After a long run of PETG: plastic stuck around the nozzle and in the wiper.
- After an abrasive spool: nozzle, extruder gears, PTFE tubes.
- When the bed suddenly fails: wash the plate before changing any setting.

## Sources

Snapmaker U1 maintenance guide (intervals used in Workshop), Prusa Knowledge Base (PEI sheet cleaning, nozzle replacement, cold pull), Bambu Lab wiki (maintenance schedules, plate care, hardened nozzles), Voron documentation (belt tension). Researched November 2026.
