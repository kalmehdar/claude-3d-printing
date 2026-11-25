---
name: filament-care
description: 'Keeps filament dry and tracked: how long and how hot to dry each material, how to store spools, how to tell a spool is wet, and how to check stock with Spoolman (`c3dp spools`) or Workshop. Use when someone says "dry this spool", "my filament is wet", "how should I store filament", "is it wet", "popping", "how much is left", "do I have enough PETG", or opens a spool that has sat out.'
---

# Filament care

The loop: **suspect it, dry it, store it dry, track what is left.** Most stringing, popping and weak layers on a spool that used to print well are moisture, not settings. Dry first, tune second (the [print-calibration](../print-calibration/SKILL.md) skill says the same).

## Is it wet?

Signs, most certain first: popping or crackling at the nozzle, steam or tiny bubbles in the extrusion, a rough or matte surface on a normally glossy filament, stringing that was not there before, weak layers, brittle filament snapping in the feed path (PLA especially). A spool left out for days in a damp room is wet until proven dry. If unsure, dry it: it costs a few hours and fixes the most common cause.

## Drying table

Use the temperature on the spool's label when it gives one; it wins over this table. Hours are for a full spool in a filament dryer; a part-used spool dries faster.

| Material | Temperature | Hours | How fast it gets wet again |
| --- | --- | --- | --- |
| PLA | 45-55 °C | 4-8 | Slowly (days to weeks) |
| PETG | 60-65 °C | 4-8 | Moderately (days) |
| ABS | 70-80 °C | 4-8 | Moderately |
| ASA | 75-80 °C | 4-8 | Moderately |
| TPU | 50-55 °C | 4-8 | Quickly (hours to days) |
| Nylon (PA) | 70-90 °C | 8-12 | Very quickly (hours): print from a dry box |
| PC | 70-80 °C | 6-8 | Quickly |
| CF / GF-filled | The base material's | The base material's (nylon-CF: 8-12) | As the base |
| PVA, BVOH (soluble supports) | 45-55 °C | 6-12 | Very quickly: keep sealed |

**Never go above the spool's limit.** Hot enough to dry nylon is hot enough to soften PLA and warp a cheap plastic spool, so the filament fuses into a lump. Cardboard spools are fine at these temperatures but shed fibre; wipe them first.

**How to dry, best first:** a filament dryer (it holds temperature and some feed while drying); a fan oven at the lowest steady setting with a separate thermometer, because many ovens overshoot by 10-20 °C at the low end (never a gas oven left unattended); a printer's heated bed under a box, only for PLA and PETG, and slowly. A food dehydrator works if it reaches the temperature.

## Storing it

- **Sealed with desiccant:** a vacuum or zip bag, or an airtight box, with silica gel. Indicating gel (orange or blue turning green or pink) shows when it is spent; dry it in the oven (check its own label, usually around 100-120 °C) and reuse.
- **Below about 20% relative humidity** for nylon, PC and PVA; under 30-40% is fine for PLA and PETG. A cheap hygrometer in the box tells you.
- **Print hygroscopic materials from a dry box**, with a tube to the printer, not from an open spool holder.
- Keep spools out of sunlight and away from heaters. Tuck the free end into the spool's edge holes so it cannot unwind and tangle.
- On a multi-head printer, spools sitting loaded for weeks soak up air too: unload materials you are not using.

## How much is left

**Spoolman** (an open-source spool tracker, self-hosted) knows every spool and its grams left. Set `SPOOLMAN_URL` to its address on your home network, then:

```bash
python3 tools/c3dp.py spools
```

lists each active spool: id, material, name, colour and grams left, by material. With `SPOOLMAN_URL` set, the `design` MCP server's slices also say whether a spool of the right material has enough left for the job. On a Klipper printer with Moonraker's Spoolman integration set up, the grams count down as it prints, without weighing.

**Without Spoolman:** Workshop keeps its own ledger per head (record a new spool and its grams in Workshop, or `POST /api/spools`; see [docs/workshop-api.md](../../docs/workshop-api.md)). It counts down from the printer's measured use, warns under 200 g, and warns before a print that needs more than the head has left.

**By weight:** gross weight minus the empty spool's weight. Empty spools vary (about 120-250 g for plastic, less for cardboard): weigh one empty spool of each brand once and note it.

## Before a long print

1. Check the grams (above) against the slicer's estimate, plus about 10% for purge and the prime tower on a multi-colour print.
2. If the spool has been out more than a day or two and the material is in the "quickly" rows, dry it.
3. Check the free end is not kinked or crossed under another turn.

## Sources

Bambu Lab wiki (filament drying recommendations and storage), Prusa Knowledge Base (drying filament, hygroscopic materials), Polymaker and Prusament datasheets (per-material drying temperatures), Spoolman project documentation (github.com/Donkie/Spoolman). Researched November 2026.
