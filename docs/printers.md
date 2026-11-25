# Other printers: Bambu, Prusa, Creality, Voron and the rest

The design tools, the skills, Print Bench and `orca.py` slicing work for any printer OrcaSlicer can slice for. Only the part
that **drives** a printer (Workshop, `printer.py`, the `printer` MCP server, watching a print) needs Klipper with Moonraker.
This page is the recipe for everything else: build a slicer setup for the printer, check it, and hand the file over.

| | Any printer | Klipper + Moonraker only |
| --- | --- | --- |
| Design, look, export, mesh check, Print Bench | yes | |
| Slice with `orca.py` presets, time and grams | yes, once it has a setup (below) | |
| Calibration tests (OrcaSlicer GUI) | yes | |
| The skills (design, calibration, troubleshooting, materials, care) | yes | |
| Send, start, pause, cancel, camera, watch, queue, Workshop | | yes |

## 1. Build a setup from the slicer's vendor profiles

`orca.py` slices with a setup folder (`machine.json`, `process.json`, one `filament-<name>.json` per material) under
`~/.config/claude-3d-printing/printers`. `orca.py new-printer` builds one from the profiles a slicer ships for your printer.

1. **Find an unpacked vendor folder.** Upstream OrcaSlicer 2.5 packs most vendors as `.opc` files that `new-printer` cannot
   read; Snapmaker Orca ships them as plain folders (Prusa, Creality, BBL, Voron, Elegoo, Qidi, Sovol and many more):
   `Snapmaker Orca.app/Contents/Resources/profiles/<Vendor>/` on a Mac. Any OrcaSlicer build under `~/tools` with an unpacked
   `resources/profiles` works too. Snapmaker Orca is free and sits beside OrcaSlicer without changing it.
2. **Pick the three names** from the folder's `machine/`, `process/` and `filament/` subfolders: the file name without
   `.json` is usually the profile's name (check its `"name"` field if `new-printer` says it is missing). Pick the machine for
   your nozzle, a 0.20 mm process, and the filaments you own.
3. **Build it.** Two that were built and sliced on 25 Nov 2026:

   ```bash
   python3 tools/orca.py new-printer core-one --vendor Prusa --machine "Prusa CORE One 0.4 nozzle" \
       --process "0.20mm STRUCTURAL @CORE One 0.4" \
       --filament "Prusa Generic PLA @CORE One:pla" --filament "Prusa Generic PETG @CORE One:petg"
   ```

   ```bash
   python3 tools/orca.py new-printer ender3v3ke --vendor Creality --machine "Creality Ender-3 V3 KE 0.4 nozzle" \
       --process "0.20mm Standard @Creality Ender3V3KE" --filament "Creality Generic PLA @Ender-3V3-all:pla"
   ```

4. **Set the plate.** Vendor process profiles usually have no `curr_bed_type`, so the slice is for OrcaSlicer's default
   Cool Plate. The CORE One setup above heated the bed to 35 °C for PLA until the plate was set. Add the plate that is
   really on the printer to the setup's `process.json`:

   ```json
   "curr_bed_type": "Textured PEI Plate"
   ```

   Smooth PEI is `High Temp Plate` in OrcaSlicer. Then check the bed temperature the slice asks for (step 6): with the plate
   set, the CORE One's Generic PLA gave 45 °C on Textured PEI and 60 °C on High Temp Plate, so compare it with the printer
   maker's own recommendation for that sheet and change the filament's `textured_plate_temp` / `hot_plate_temp` if needed.
5. **Match the start and end G-code to a print that worked.** Slice something in the maker's own slicer (Bambu Studio,
   PrusaSlicer, Creality Print, Snapmaker Orca) that prints well, then:

   ```bash
   python3 tools/orca.py adopt-gcode good-print.gcode --printer core-one
   ```

   It copies only the custom G-code blocks (start, end, tool change, layer change) from that file's settings block. It needs a
   file from OrcaSlicer or a fork of it (Bambu Studio, Snapmaker Orca, Creality Print 6 and later); PrusaSlicer names the
   same settings differently (`start_gcode`, not `machine_start_gcode`), so nothing would copy; for a Prusa, slice the good print in OrcaSlicer with the Prusa profile instead.
6. **Prove it.** Slice the fit card and read the first heater lines:

   ```bash
   python3 tools/orca.py slice examples/models/fit-card.scad --printer core-one --material pla --preset standard
   ```

   The first `M140`/`M190` is the bed and must match the plate; the `M104`/`M109` after the start sequence is the nozzle.
   (`orca.py` refuses a bed of 0 °C, but not a bed that is merely too cold.)
7. **Optional:** `python3 tools/orca.py install --printer core-one` puts the setup and the `Claude ...` presets into
   OrcaSlicer's own menus.

**A vendor folder with no filaments** (Voron's has none): `add-material` cannot yet take filaments from
`OrcaFilamentLibrary`, whose profiles inherit from a base kept elsewhere. In the OrcaSlicer app, pick the printer and a
filament, then File > Export > Export preset, and save that filament JSON into the setup folder as `filament-pla.json`
(or `filament-<name>.json`).

### Which presets carry over

Every `orca.py` preset (`draft`, `standard`, `strong`, `solid`, `fine`, `quality`, `smooth`, `figure`, `polished`, `lean`,
`grip`, `fine-text`) only changes process settings: layers, walls, infill, seams, speeds, ironing. They work on any printer.
Checked on 25 Nov 2026: `standard`, `strong` and `quality` all sliced on the CORE One and the Ender-3 V3 KE setups above.

What to keep in mind:

- **Speeds and accelerations** in `quality`, `smooth`, `figure` and `fine-text` (for example an 80 mm/s outer wall at
  3000 mm/s²) are asked for, not forced: the printer's firmware limits still apply (Klipper's `max_velocity` and `max_accel`,
  Marlin's M201/M203), so a slower printer prints them slower.
  The numbers were tested on a Snapmaker U1; check the first print's outer wall and seam.
- **`low-waste` and `lean`** cut the prime tower and the prime at each head change: only meaningful on a multi-head printer
  or tool changer. On a single-nozzle printer with an AMS-style feeder (Bambu AMS, Prusa MMU, Creality CFS), colour changes
  purge through one nozzle, so cutting the purge causes colour bleed: leave it to the maker's flush settings.
- **The filament and plate** (temperatures, flow, pressure advance) come from the setup, not the preset. Calibrate them
  with [print-calibration](../skills/print-calibration/SKILL.md).

## 2. Hand the file over: printers Claude cannot drive

Claude does not send to, start or watch these printers from this repo. It slices, checks the file and tells the person how
to send it; the person starts the print. Every rule in [for-claude.md](for-claude.md) about an empty bed still applies: it
is the person's check at the printer.

`orca.py slice` leaves the files in its `--out` folder: `plate_1.gcode` and a project `.3mf` with the settings inside.

| Printer runs | Send it like this |
| --- | --- |
| **Bambu Lab** (cloud or LAN mode) | Open the project `.3mf` in **Bambu Studio** (or OrcaSlicer with the Bambu printer added) and press Print there; on a P1 or X1 you can also copy the `.gcode.3mf` to the SD card. Bambu's network protocol is not documented for third parties and recent firmware limits outside access, so this repo does not talk to it. The `bambu-p1s` setup and [bambu-3mf-export.md](../skills/3d-print-designer/bambu-3mf-export.md) make files that Bambu Studio opens with colours and settings intact |
| **PrusaLink** (MK4, MK3.9, CORE One, XL, MINI) | Upload from the PrusaLink web page on the printer's address (Upload), PrusaConnect, or a USB stick. Or upload from the command line with the printer's API key, kept in an environment variable (example below), then start it at the printer |
| **OctoPrint** (any Marlin printer with an OctoPrint host) | Drag the `.gcode` into OctoPrint's file panel, or upload with its API key (example below), then press Print in OctoPrint |
| **Creality** (Ender-3 V3 KE, K1, K2 and others) | Creality Print or the printer's own web page, or a USB stick or SD card. The Klipper-based models run Creality's own firmware; Moonraker access usually needs root or community firmware, after which Workshop may work (untested here) |
| **Plain Marlin** with no host | Copy the `.gcode` to the SD card or USB stick |
| **Any other Klipper printer** with Moonraker | Full support: see the README's Workshop setup |

Command-line uploads (the person runs these, with the key in an environment variable, never pasted into a file or chat).
Both only upload; starting stays at the printer. Check them against your version's API docs:

```bash
curl -H "X-Api-Key: $OCTOPRINT_API_KEY" -F "file=@plate_1.gcode" "http://<octoprint-address>/api/files/local"
```

```bash
curl -X PUT -H "X-Api-Key: $PRUSALINK_API_KEY" -H "Content-Type: application/octet-stream" --data-binary @plate_1.gcode "http://<printer-address>/api/v1/files/usb/plate_1.gcode"
```

Prusa's newer printers also print binary G-code (`.bgcode`), which PrusaSlicer writes by default; OrcaSlicer writes plain
`.gcode`, which they print too.

## 3. Calibration and troubleshooting on any printer

[print-calibration](../skills/print-calibration/SKILL.md) uses OrcaSlicer's own calibration tests, which work for any printer
OrcaSlicer supports: run the test from the app, send it as above, and write the number into the setup's filament file.
[print-troubleshooting](../skills/print-troubleshooting/SKILL.md) works from a photo the person takes. Without a printer
camera Claude cannot watch a print; ask for a photo after the first layers of anything long.

## Sources

OrcaSlicer wiki and source (vendor profiles, `curr_bed_type`, calibration), Snapmaker Orca's bundled profiles (read on
25 Nov 2026), OctoPrint REST API documentation (file upload), PrusaLink OpenAPI specification (`/api/v1/files`), Bambu Lab
wiki (LAN mode, sending from Bambu Studio). The setups and slices above were built and run on 25 Nov 2026.
