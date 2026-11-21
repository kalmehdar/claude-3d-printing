---
name: print-troubleshooting
description: 'Diagnoses a failing or bad FDM print from a photo, a printer camera frame (printer_snapshot, printer.py snap) or a description, decides whether to keep going, pause or cancel, and gives the slicer or printer fix. Use when someone says "is my print OK", "what went wrong", "why is it stringy", "it came off the bed", "spaghetti", "the layers shifted", "check the print", or sends a photo of a print, and during a watched print when a frame looks wrong.'
---

# Print troubleshooting

The loop: **look, place it, decide, fix, check the next frame.** Look before you judge: read the photo or frame yourself, and say what you can and cannot see. A low camera (the Snapmaker U1's sits at bed level) shows the first layers and big failures, not fine surface defects; a person's close photo is better for those.

## 1. Place it first

Where the defect starts tells you most of the cause:

- **At the bed** (first layers): adhesion. Section A.
- **At one height**, everything above it wrong: a shift, a clog or the part moving. Sections B to D.
- **Everywhere, evenly**: tuning (flow, temperature, speed, retraction). Sections E to H.
- **Only at colour or tool changes**: purge and prime tower. Section I.

## 2. Decide: keep going, pause, or cancel

Cancel when continuing makes it worse or risks the printer; pause when a person should look; keep going for cosmetic defects. The calls below are judgement unless a source is named. Pause or cancel only when you are confident from what you see; when unsure, say so, keep watching, and tell the person. Before a cancel you start yourself, take a second fresh frame a few seconds later and check it shows the same thing (and its timestamp is new), then tell the person at once.

| Seen | Call |
| --- | --- |
| Spaghetti, part loose or knocked over, extruding into air | **Cancel now**. Cancelling just one object (Klipper `EXCLUDE_OBJECT`) is for the person to do from Fluidd or the printer screen; Claude never forces raw G-code mid-print |
| First-layer lines not bonded; a corner lifting and getting worse | **Cancel** and restart after the fix (the nozzle will hit the curl) |
| Layer shift | **Cancel**: everything above is out of place |
| Sudden thin or missing extrusion mid-print, extruder clicking | **Cancel**: clog or heat creep; it prints air, then spaghetti |
| Toppled or detached prime tower | **Cancel**: the next tool change spaghettis |
| Plastic building up on the nozzle (PETG) | **Pause**, clean the nozzle |
| Overhang edges curling up where the nozzle passes | **Pause** and look |
| Stringing, blobs, ringing, banding, elephant foot, mild over- or under-extrusion | **Keep going**, fix it for next time |
| Printing with the bed heater off (bed target 0) | **Pause** (`printer.py watch` does this itself, but only while it is running) |

After any pause or cancel, tell the person what you saw, at which layer, and what happens next.

## 3. What it looks like, why, and the fix

Setting names are OrcaSlicer keys. Before you change one, confirm it exists for this printer: `python tools/orca.py settings <word> --printer <setup>` (it lists the plain names and searches the profile's own keys). Change one thing at a time.

**A. Not sticking, warping, corner lift.** First-layer lines round and separate, ends peeling, corners rising, a brim curling. Causes, most likely first: nozzle too high (Z offset), dirty plate (skin oil), bed too cool for the material, first layer too fast, wrong filament profile (PETG on PLA settings will not stick), fan on too early, small contact area. Fix: clean the plate (soap and water or IPA), raise bed temperature 5 to 10 °C, slow the first layer, keep the fan off for the first layers (`close_fan_the_first_x_layers`), add a brim (`brim_type`, `brim_width`), check the profile matches the spool. Also check the slicer actually heated the bed: on the U1 a plate type the filament has no temperature for slices at 0 °C (see docs/snapmaker-u1.md). Sources: Simplify3D, Prusa.

**B. Spaghetti, detached part.** Loose tangles where the part should be. Causes: the part came off the bed (see A), lower layers collapsed, a model or G-code error, a clog. Fix the cause in A, add a brim or supports, check the toolpath preview before printing. Source: Prusa ("spaghetti monster").

**C. Layer shift.** A step in the wall: every layer above one height offset sideways. Causes: moving too fast so a motor skips, loose belts or pulley screws, the nozzle hitting a curled edge or blob, overheated drivers. Fix: lower speed and acceleration, check belts and pulleys, stop the curling (A, K). Sources: Simplify3D, Bambu wiki.

**D. Clog, heat creep.** Extrusion thins or stops mid-print while moves continue; clicking or grinding. Causes: debris or burnt plastic in the nozzle (the usual one), heat creeping up the heatbreak (warm enclosure with PLA, weak heatsink fan, very slow flow). Fix: cut the filament end and reload, a cold pull, check the hotend fan; on a tool changer, the head's own clean routine. Sources: Simplify3D, Prusa.

**E. Under-extrusion.** Gaps between walls and infill, thin see-through top layers, weak rough walls (a 20 mm cube with 3 walls shows gaps at the top). Causes: partial clog, wrong filament diameter, flow ratio too low (`filament_flow_ratio`), too cold, faster than the filament can melt (`filament_max_volumetric_speed`), wet filament, extruder slipping. Fix: run the calibration skill's flow and max-speed tests, raise temperature 5 to 10 °C, dry the spool. Source: Simplify3D.

**F. Over-extrusion.** Bulging blobby walls, oversized parts, ridges on top. Causes: flow too high, wrong diameter; bulging corners point to pressure advance. Fix: flow calibration, pressure advance calibration. Source: Simplify3D.

**G. Stringing.** Fine hairs or webs between separate features. Causes: wet filament (popping, matte strings: the Bambu wiki's first cause), too hot, retraction too short or off, long travel over open space. Fix: dry the spool, lower temperature 5 to 10 °C, retraction (direct drive about 0.5 to 2 mm), avoid crossing walls. PETG strings more than PLA by nature. Sources: Simplify3D, Bambu wiki.

**H. Surface patterns.** *Ringing*: ripples echoing a sharp corner or hole, just after it (too much speed or acceleration, loose frame or belts; fix with lower acceleration or input shaping). *Z-banding*: evenly spaced horizontal ridges on every wall, even plain ones (uneven filament, temperature swing, wobbling bed or Z axis). *Elephant foot*: the bottom layers flare out (nozzle too close, bed too hot; `elefant_foot_compensation`, spelt that way). *Blobs and zits*: bumps along the seam (`seam_position` to a hidden side, pressure advance, wipe). All cosmetic. Sources: Simplify3D, Klipper resonance docs, OrcaSlicer wiki.

**I. Tool changes and colours.** *Colour bleed*: the old colour tints the new one (purge too small, worst dark to light). *Ooze*: blobs or strings at changes (idle heads cooling and oozing). *Prime tower toppled or stringy*: the next change fails. Fix: raise purge for the problem colour pair, keep the prime tower, slow or widen it if it wobbles. Snapmaker U1 owners report a 25 mm wide tower and a smaller minimal purge cut waste, and a purge line for every head in use helps first layers; treat those as community tips to test, not settings to trust.

**J. PETG.** Strings and blobs (dry it; tune retraction), plastic gathering on the nozzle and being wiped onto the part (pause, clean), sticking too hard to smooth PEI (do not squash the first layer; a release layer such as glue stick helps; use the textured plate). Mostly forum knowledge: verify on the printer.

**K. Bridges and overhangs.** Sagging strands in a bridge, rough or curled undersides. Causes: too long a span, too little cooling, bridge settings not applied. Fix: supports (`enable_support`), more bridge cooling, a slightly cooler nozzle; in the design, chamfer at 45° under ledges (see 3d-print-designer). Source: Simplify3D.

## 4. Check the fix

Name the one change, re-slice, and on the next print look at the frames where the fault appeared. Record what worked with the design (and in the print log) so the next print starts from it.

## Sources

Simplify3D Print Quality Guide (simplify3d.com/resources/print-quality-troubleshooting), Prusa Knowledge Base (spaghetti monster, elephant foot, heat creep), Bambu Lab wiki (layer shifts, stringing), Klipper resonance compensation docs, OrcaSlicer wiki (setting keys, calibration tests), Snapmaker forum threads on the U1 prime tower (community reports).
