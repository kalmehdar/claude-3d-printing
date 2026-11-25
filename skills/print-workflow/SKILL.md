---
name: print-workflow
description: 'Takes a 3D print from idea or existing STL to a checked, multi-colour, previewed set of print files using the claude-3d-printing tools: OpenSCAD per-colour parts, export with a bed check, a Blender mesh audit, a Print Bench page with sizes and a measure tool, and on a networked Klipper/Moonraker printer (Snapmaker U1 and others) sending, starting after a camera bed check, watching and logging the print. Use when someone says "print this", "send it to the printer", "is the print OK", "watch the print", or when someone says "design me a ...", "make this print in two colours", "add a label to this STL", "upgrade this print", "remix this", "show me the prints", "build a print bench", "check this STL", or hands over an STL to improve.'
---

# Print workflow

The loop: **ask, model, export, audit, preview, hand over, and print it** when Claude can reach the printer. Design rules (wall thickness, overhangs, tolerances, materials) come from the `3d-print-designer` skill; this skill is the pipeline around them and the house style that makes the result look finished.

The tools ship with this skill: `tools/` sits two folders above this skill's base directory (the plugin root, or the repo root in a clone). Use that path in every command below; if the skill was copied on its own without `tools/`, clone github.com/kalmehdar/claude-3d-printing and use its `tools/`.

First time on a machine: `python tools/c3dp.py doctor` says what is missing (OpenSCAD, OrcaSlicer, Blender, measured fits, printer setups). In the Claude desktop app, with no terminal, the `design` MCP server (`tools/design_mcp.py`) gives the same steps as tools: `design_save`, `design_look`, `design_export`, `design_slice`, `design_fit`, `design_project`.

## 1. Ask (one message, then stop)

- The printer and its bed size, the nozzle, how many colours it can print and the material. Skip what the user already said.
- What the part must fit. Anything that clips on, slides in, screws to or holds something real needs a caliper number from the person. Never guess a fit size: write it as a named variable marked `// FIT: measure ...` and say so.
- Clearances come from the printer, not the material: run `python tools/fit.py show`. Measured fits replace the designer skill's material defaults (`python tools/fit.py scad` gives the lines to paste). If it says "not measured" and the design has a moving or press-fit part, say so once and offer the fit card (`examples/models/fit-card.scad`, about 20 minutes); when the person reports the smallest hole the peg turns in and the smallest it presses into, store them with `python tools/fit.py set --slide <mm> --press <mm> --printer "<printer, material>"`. A design with M3 heat-set inserts uses `insert_m3_hole` from the same output; if it is not measured, offer the insert card (`examples/models/insert-card.scad`, about 20 minutes) and store the smallest hole where the insert sits flush and does not turn with `--insert <mm>`.
- For an upgrade: the STL, what is wrong with it or what to add, and its licence. Read the licence on the model's page (Printables, MakerWorld, Thingiverse all show one) before remixing: CC0, CC BY and CC BY-SA allow remixes with credit (BY-SA remixes keep BY-SA); NC means personal use only, never sold; ND (no derivatives) and the MakerWorld "Standard Digital File License" do not allow a published remix, so say so and only change it for the person's own use; no licence shown means ask the owner first. Credit the original in the design's header and on the bench.

## 2. Model (OpenSCAD, one file)

- Every size a named variable at the top, `$fn = 48`, comments saying what each number is. Sizes that change between prints also get named sets in `model.json` (`tools/param_sets.py`), so OpenSCAD's Customizer offers them.
- One `part` variable selects a colour, and the first line lists them: `// parts: body label accent`. Add `part = "all"` for a preview with `color()` on each part.
- Labels are printed in, never stuck on: a 0.6 mm pocket in the body, the same text clipped to the part as the second colour, printed face down (so mirror the text on a bottom face).
- Same-colour shapes that touch only along an edge or a corner make non-manifold meshes: overlap them by 0.01 mm.
- Upgrading an STL: `import("file.stl", convexity = 6)`, then `difference()` to cut (pockets, holes, label recesses), `union()` to add (hooks, tabs, feet, ribs), `intersection()` to lift a region out as its own colour, removing it from the body with `difference()`. Read the STL's size and bounds first (run `export_parts.py` on a file that imports it, or `mesh_audit.py` on its folder) and place everything from those numbers. `examples/models/upgrade-bin.scad` is the pattern.

**Look before exporting:** `python tools/look.py model.scad` (add `--part <name>` or `-D name=value` as needed) writes `model-look.png` with angled, top, front and side views in colour and prints the real size in mm. Read the picture and compare it with the brief: sizes, which face is down, label direction, nothing floating. Fix, then look again.

## 3. Export

```bash
python tools/export_parts.py model.scad --out stl --bed 256x256x256
```

One binary STL per part, each checked against the bed. Set `OPENSCAD` if it is not on PATH; a development snapshot uses the manifold backend and is much faster. A part too big for the bed: split it with `intersection()` and add alignment pins or a dovetail at the joint.

## 4. Audit

```bash
blender --background --factory-startup --python tools/mesh_audit.py -- stl --bed 256
```

It must end `0 file(s) need a fix`. A `check <file>: ... above the plate` line means a piece hangs in the air (the slicer reports "empty first layer"): fix it in the model before slicing. A solid body should be `1` piece; lettering is many by design. A large thin-area number on a solid part means look at that spot in the slicer. A `check` note is printability, not a fault: an overhang over 100 mm2 (fix in the model first: a 45 degree chamfer, a teardrop hole, or turn the part; supports last) or a bridge over 15 mm (turn it so the span is shorter, or add a rib under it). Fix it before slicing when the fix is cheap, and say which you left. No Blender: say the audit was skipped, never that the mesh is fine.

## 5. Preview: the Print Bench

Add the design to a `bench.json` (format in `tools/print_bench.py`) with its parts in their real filament colours, then:

```bash
python tools/print_bench.py bench.json print-bench.html
```

Look at it yourself before showing it: the list picture, the colours, the size lines (width, depth, height in mm), and use Measure to check one critical distance against the spec. For an upgrade, list the original as "before" next to it. To share it publicly, put it on GitHub Pages (the repo's `pages.yml` workflow does this on every push to main); a Claude artifact link only opens inside the owner's account.

Use these colours so every bench looks like one set: black `#1f1f22`, white `#ecebe6`, red `#c8102e`, blue `#002fa7`, grey `#8a8f98`. Use the real filament colour where it differs.

## 6. Hand over

Make the slicer project so nobody maps colours by hand:

```bash
python tools/make_3mf.py bench.json <model id> <name>.3mf      # or: --part body.stl:#1f1f22 --part label.stl:#ecebe6
```

It opens in Bambu Studio and OrcaSlicer (and their forks) as one object with every part on its filament slot and the slot colours set. The person still picks the printer, process and filament types in the slicer. If the parts plus a prime tower don't fit the bed, split them across two projects.

- The 3MF project, and which slot is which colour (the tool prints the list), the orientation (label faces down on the plate), supports or none and why, and anything still marked FIT.
- Slice it yourself when OrcaSlicer is installed, and give its time and grams, never a guess:
  `python tools/orca.py slice <name>.3mf --printer <setup> --material petg-black,petg-red --preset standard [--set infill=30 walls=4 supports=on]`.
  Presets: draft, standard, strong (load-bearing), solid (100% infill, the strongest), fine, quality (clean walls and parts that fit for 3-12% more time: gentle outer walls, Arachne, precise outer wall, seam at the back), polished (quality plus ironed flat tops, 12-40% more time), lean (clean outside, lightning infill, a 15 mm3 prime: about standard's time and 11-30% less filament; not for load-bearing parts), low-waste (multi-colour on a tool changer). `python tools/orca.py settings` lists the
  plain names; any other OrcaSlicer key works too and a misspelt one is refused with suggestions. `orca.py open` does the same and opens the
  result in the app for the person to check or send. A printer setup is a folder of exported presets (`orca.py printers`); add a filament
  with `orca.py add-material "<part of its name>" --printer <setup>`, and `orca.py install --printer <setup>` puts the presets in the app's menus.
  No OrcaSlicer: say so, and the slicer's numbers beat the bench's estimate (about 20% either way).
- No networked printer (step 7): the person starts the print and watches the first layer.

## 7. Print it (a Klipper/Moonraker printer on the network)

**In the Claude app with the printer tools** (`printer_status`, `printer_snapshot`, `printer_slice`... from `tools/printer_mcp.py`), use those instead of the commands below: the same loop, with Workshop's safety checks and the person's name in the activity log. `printer_design_picture` shows a design before slicing; `printer_layer_photos` shows how a long or overnight print went, one photo per layer; `printer_start` needs `bed_clear` from the person's own words, after `printer_snapshot` shows an empty plate.

When the person has a printer Claude can reach (`python tools/printer.py status` answers; the address comes from `--printer`, `PRINTER_HOST` or `~/.config/claude-3d-printing/printer.json`), Claude can run the print itself. The loop: **send, look, start, watch, log.**

1. **Match the loaded filament.** `printer.py status` lists what is on each head (the Snapmaker U1 reports type and colour per slot). Map each design colour to the head that holds it, and slice for those materials. A colour that is not loaded: say so, never print it in the wrong colour.
2. **Send.** `python tools/printer.py send <name>.gcode --queue` uploads and queues. It refuses to queue while the queue is running, because a running queue would start the job onto the last print; `queue hold` first.
3. **The person says go, the camera agrees.** Every start needs both: the person has said the bed is clear and that they are around (or has told you in words that you may start on a clear camera check), and a fresh `python tools/printer.py snap bed.jpg` shows the plate on, nothing on it, no purge blobs or a finished part. A camera low at bed level can miss a thin leftover part, so the picture alone never starts a print. Then `printer.py start <name>.gcode --bed-clear` (or `queue start --bed-clear`, which starts one job and holds the queue behind it). Never pass `--bed-clear` on a guess.
4. **Watch.** `python tools/printer.py watch 60 --frames frames/` writes one camera frame per layer and stops when the job ends. Look at the first-layer frames yourself, then every few layers. Cancel (`printer.py cancel`) and tell the person at once on: spaghetti or loose strands, a corner lifting, the part moving on the plate, the nozzle dragging through the print, or a blob building on the head. Pause (`printer.py pause`) instead when it might be fine and the person should look. Tell them where they will see it: `printer.py notify "Print paused: <file>" "<what you saw, which layer>" --level warn` (or `bad` after a cancel) posts to their Discord when a webhook is set up, and `watch` posts by itself when the job ends. A camera low at bed level only shows large failures; say so rather than calling a print fine from it.
5. **Filament.** On a Snapmaker U1, `printer.py unload T2` (or `all`) runs the printer's own unload: it heats that head, cuts the tip and winds the filament back to its spool. Only when no job is running; soft filament (TPU) is left for the printer's screen, as the firmware does. Loading needs a person to feed the new filament; once they say what they loaded ("black Polymaker PETG in T2"), set it so they do not have to on the screen: `printer.py filament T2 PETG --vendor Polymaker --sub generic --colour 1f1f22` (`printer.py filament list` shows the names the printer knows; turn a colour name into its hex, ask when unsure). Snapmaker's chipped spools set themselves from their tag. Read `printer.py status` back to confirm.
6. **Log.** After the job, `printer.py history 1` gives the real minutes and metres; record them with the design (and in the person's print register if they keep one).

The person can follow the same printer on `python tools/workshop.py --printer <address>`: camera, queue, toolpath preview before printing, and every bench design with a slice-and-send button.

**A slicer setup that matches the printer.** `orca.py new-printer` builds a setup from a slicer's bundled vendor profiles (Snapmaker Orca ships them unpacked), and `orca.py adopt-gcode good-print.gcode --printer <setup>` takes the start, end and tool-change G-code from a print that came out right. Use that when a fork's profile uses variables upstream OrcaSlicer refuses (the U1's `chamber_cooling_mode`).

**Tool changers (Snapmaker U1, Prusa XL).** Each head keeps its own colour, so the `low-waste` preset's small prime tower is usually enough; check the first print for colour bleed. Group colours so the most-used one prints from the head with the freshest filament.

**Painted multicolour 3MFs made for another printer** (MakerWorld, Bambu X1/H2D projects; 24 Nov 2026). `orca.py` flattens
them to one colour, and the original fails in the CLI ("run found error"). Rebuild a clean 3MF: take the plate-1 object's
mesh, apply its build transform, centre it on the bed with z from 0, and keep each triangle's `paint_color`, remapping the
designer's slots to the loaded heads (Bambu codes: 4 = slot 1, 8 = slot 2, 0C/1C/2C = slots 3/4/5; split triangles nest the
same codes, so decode the tree, never string-replace). Set the object's default `extruder` and a minimal
`project_settings.config` (filament colours, support keys). Check the designer's colour list first: a grey only used on one
shoe can map to white instead of a head you do not have. Workshop now does this (claude-3d-printing #35).

**Figures on the U1.** The `figure` preset (0.16 mm, the gentle outer wall), PLA as the support interface under PETG
(`support_interface_filament` = the PLA head, `support_top_z_distance` 0, `support_interface_spacing` 0, 3 interface layers),
the tower as in print-troubleshooting I. PLA contact costs about 300 more swaps and 45 to 80 min on a ghost-sized figure.
Whether a tool changer needs a tower at all is under test (a white/black block with no tower, 24 Nov).

## Habits that save prints (from experienced makers, Nov 2026)

- **Dry PETG before it matters.** Damp PETG strings, blobs and fails, and a UK room sits at 40 to 50% humidity. Strings on a
  print that was fine before: dry the spool first (a filament dryer, about 65 C for PETG, 4 to 6 hours), then touch settings.
- **Let the plate cool, then flex it.** Pulling PETG or a long thin part off a warm plate warps it; a cool part pops off.
- **Never touch the print surface.** Finger oil is the commonest first-layer failure; wash the plate with washing-up liquid
  and water, dry it, and hold it by the edges. A scraper goes in at the shallowest angle that works, or the PEI scratches.
- **Orientation first, settings second.** The face on the plate takes the plate's texture; the best-looking face goes down
  on a smooth plate, or up with ironing (`polished`). Layer lines hide best on curves facing sideways, not on flat tops.
- **Don't rush PETG.** Faster profiles cost more in failures than they save; `lean` and `quality` keep slow outer walls.
- **To hide layer lines:** orientation, a filled filament (PETG-CF hides them best), `grip` for held parts, and for a show
  finish sand, prime and paint. A smaller nozzle helps but costs a lot of time.

**On the U1 specifically** (U1 videos, Nov 2026):
- **Supports in a different material** peel off cleanly: PLA supports under a PETG part (or the reverse) do not bond, so the
  support gap (`support_top_z_distance`) can go to about 0. Same material: keep about 0.2 mm.
- **Check a downloaded model's filaments before slicing.** Snapmaker Orca's filament sync maps the designer's filaments onto
  yours by colour; a support designed in PETG quietly becomes your PLA and welds on. Read the source filament list first.
- **Lettering:** `fine-text`, bold font, text face up (a textured plate blurs face-down text).
- **Nozzle sizes:** 0.2 to 0.8 mm hot ends exist, but one print uses one size on all four heads. The high-flow hot end is
  not for carbon or glass fibre; abrasive filament needs the hardened-steel hot ends.

## Done means

Exported parts within the bed, the audit at 0 to fix, the bench rebuilt and looked at, and every FIT number either measured or listed for the person to measure. When Claude ran the print: the bed seen empty before the start, the first layers looked at, and the real time and metres logged.
