---
name: print-calibration
description: 'Calibrates a filament or a new printer with OrcaSlicer''s built-in tests (temperature, max volumetric speed, pressure advance, flow ratio, retraction, tolerance), reads the printed result and writes the number into the right profile key. Use when someone says "calibrate this filament", "new spool", "tune flow", "pressure advance", "temp tower", "my prints are stringy or bulging after changing filament", or sets up a new printer or a tool changer head.'
---

# Print calibration

The loop: **one test, read it, write one number, save the profile, next test.** Dry the spool first: wet filament ruins every test that follows.

## Order

New filament: 1 temperature, 2 max volumetric speed, 3 pressure advance, 4 flow ratio, 5 retraction. If flow ratio moved a lot, rerun a quick pressure advance. (OrcaSlicer calibration guide.)

New printer: check the firmware's extruder steps are right, then run the filament list on one known filament, then the printer-level extras if needed: input shaping (use the printer's own routine when it has one; the U1 has vibration compensation), cornering, VFA, tolerance last.

## The tests (OrcaSlicer menu: Calibration)

The tests exist only in the GUI. The CLI has no calibration flags, and the per-layer changes (temperature, PA, retraction, speed) are added at slice time, so slicing the raw test model from the command line does not give you the test. Open the test in OrcaSlicer, then use `printer.py send` and `start --bed-clear` as normal. After a test, start a new project so calibration mode does not leak into the next print.

| Test | Read it | Write it to |
| --- | --- | --- |
| Temperature tower (5 °C per block) | Block with the least stringing and best overhangs and bridges. If a range looks good, take the middle; the higher end if you print fast | `nozzle_temperature`, `nozzle_temperature_initial_layer` |
| Max volumetric speed (default 5 to 20 mm³/s, step 0.5) | Measure the height where gloss goes, then where curves deform. Value = start + height x step. If it reached the top, rerun with a higher end | `filament_max_volumetric_speed` a little below the deform point (10 to 20% margin); `filament_max_external_volumetric_speed` just below the gloss point |
| Pressure advance: tower, pattern or line | Tower: height (mm) of the best corners; PA = start + step x height (0 + 0.002 x 8 = 0.016). Pattern or line: the best numbered line | `pressure_advance`, with `enable_pressure_advance` = 1 |
| Flow ratio, YOLO (11 blocks, -0.05 to +0.05) | Smoothest top, no gaps between arcs | `filament_flow_ratio` = current + modifier (0.98 + 0.01 = 0.99) |
| Retraction tower (direct drive 0 to 2 mm, step 0.1) | Lowest clean height. Find its length in the G-code comment `; Calib_Retraction_tower: Z_HEIGHT: ..., length: ...`, not `; retract`. Strings even at the top mean dry the spool | `filament_retraction_length` |
| Tolerance (right-click plate, Add Handy Model, Orca Tolerance Test) | Which hex hole a 6 mm Allen key fits | Hole and contour compensation; or keep using `tools/fit.py` |

**Driving the GUI (Snapmaker Orca, seen 21 Nov 2026 on macOS):**
- Each test opens as its own project; the rest of the Calibration menu greys out until you start a new project (Cmd+N). Answer "No" to saving and "Discard" to the temperature changes the test made to the preset.
- Export with Cmd+G. It saves a `.gcode.3mf`; the G-code is inside at `Metadata/plate_1.gcode` (`unzip -p file.gcode.3mf Metadata/plate_1.gcode > test.gcode`).
- Before sending, check the extracted file: the first `M190`/`M140` is the bed (not 0), `; filament_settings_id` names the intended profile on the right head, and the temperature tower's `M104 S<n> ; set nozzle temperature` lines step through the range you asked for.
- The setup wizard and the main window are web views a screen reader can't see. Drive them by screen position, with the screen awake and unlocked and nobody else using it.

Before writing a key, confirm it exists for this printer: `python tools/orca.py settings <word> --printer <setup>`. Save the filament profile after every step, and note the values in the print log.

## Snapmaker U1

- **Four heads, four filaments.** Calibrate per head and filament; each head's PA is separate (`extruder`, `extruder1`, `extruder2`, `extruder3`).
- **The printer has its own PA calibration**, `FLOW_CALIBRATE` (Snapmaker's `flow_calibrator.py`). Despite the name, it measures pressure advance K, not flow ratio. It runs on the active head, stores the result per loaded filament, and resets on unload or filament change (`FLOW_RESET_K`). Flow ratio still comes from the slicer profile.
- **Check what the heads actually use** (21 Nov 2026): `printer/objects/query?extruder=pressure_advance` read 0.0 on T0 and T1 and 0.04 on T2 and T3, with `enable_pressure_advance` = 0 in every profile, so nothing was setting PA and the black PETG had printed every job without it. Read it live before and after calibrating.
- **Pick one PA source per filament.** A PA value sent by the slicer overrides the measured one, and Snapmaker Orca sends it by default (the setting's exact name is unconfirmed). Either tune PA in OrcaSlicer and keep the slicer value, or rely on `FLOW_CALIBRATE` and stop the slicer sending PA. Forum users found the two disagree (PETG 0.033 measured against 0.051 by hand), so do not mix them.
- `FLOW_CALIBRATE` heats and moves the head: it is a real job, not a probe. Run it only with the person's go and an empty bed, like a print.

## Without the GUI: numbered tests the person reads by eye (tested on the U1, 24 Nov 2026)

A tower read "at what height" makes the person measure. Print separate numbered pieces instead and ask "which number is best":
one object per value, each with its number raised on a small tile, all sliced together, then a per-object command inserted
after each `EXCLUDE_OBJECT_START NAME=<obj>` line in the G-code (Orca labels objects when `exclude_object` is on).

| Test | Pieces | Inserted after each object's start | Read |
| --- | --- | --- | --- |
| Pressure advance | 8 chevrons (a 90 degree V, 10 mm tall, 1 wall, no infill, outer wall 100 mm/s) | `SET_PRESSURE_ADVANCE ADVANCE=0.0n` | the crispest V tip; a tie means the midpoint |
| Flow ratio | 9 flat tiles (18 mm, 1.6 mm thick, 100% infill, 5 top layers) per colour | `M221 S<new/current x 100>`, and `M221 S100` after `EXCLUDE_OBJECT_END` | the smoothest top: lines flat and touching |
| Temperature | one banded tower per colour (6 mm bands with a 10 mm bridge and a 45 degree overhang, the temperature raised on the front), printed by object | `M104 S<band>` at each band's first `;Z:` (M109 when a tower starts), and the slicer's own `M104/M109 S<profile temp>` lines rewritten to the band's | the band with the cleanest bridge and least string |

- Put each object on the right head by writing the 3MF yourself (`model_settings.config` `extruder` per object), slicing
  with all four materials: an STL sliced alone lands on T0.
- The slicer still emits one `SET_PRESSURE_ADVANCE` for the profile value at the start; the per-object lines after it win.
- Small pieces slow to the minimum layer time: an 8-chevron test estimated 24 min took about 30.
- If the best piece is at the end of the range, rerun around it before saving (24 Nov: white flow picked 0.99, the top tile).
- Results so far (SUNLU PETG on the U1): black pressure advance 0.07 (was 0.04), flow white 0.99 and black 0.98 (both were
  0.95, under-extruding). White pressure advance and both temperatures not yet tested.
- Workshop's one-tap calibration (claude-3d-printing #37) builds these and turns the person's answer into a profile pull request.

Klipper's `TUNING_TOWER` also works (`TUNING_TOWER COMMAND=SET_PRESSURE_ADVANCE PARAMETER=ADVANCE START=0 FACTOR=.0025`, PA = 0.0025 x height),
but the person has to measure a height, which is why the numbered pieces replaced it.

## Sources

OrcaSlicer wiki (calibration pages, calibration guide, CLI pages), OrcaSlicer source (`resources/calib/`, `PrintConfig.cpp`; latest release 2.5.1), Klipper G-code docs, Snapmaker/u1-klipper `flow_calibrator.py` and `print_task_config.py`, Snapmaker forum threads on U1 dynamic flow calibration. Researched November 2026.
