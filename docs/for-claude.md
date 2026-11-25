# For Claude: setting this repo up for someone and using it well

You are setting up claude-3d-printing for a person, or working in it for them. Read this whole page first. It is written
for any Claude (Claude Code, the Claude desktop app with MCP, or an agent) and any printer; nothing here assumes one person
or one machine.

## 1. Install

1. **Prefer the plugin.** In Claude Code: `/plugin marketplace add kalmehdar/claude-3d-printing`, then
   `/plugin install print-bench@print-bench`. It brings the skills, the agents and both MCP servers (`design`, `printer`),
   and asks the person for the Python command, a designs folder, and optional Spoolman and Workshop addresses.
   If plugins are off: clone the repo and copy `skills/` and `agents/` into `~/.claude/`.
2. **Check the tools.** `python3 tools/c3dp.py doctor` (on Windows `python`). Install what it reports missing:
   OpenSCAD (a development snapshot: the manifold backend renders in seconds), a slicer (OrcaSlicer, or the printer maker's
   fork such as Snapmaker Orca or Bambu Studio), Blender for the mesh check (optional). On a Mac `bash tools/install-mac.sh`
   does all of it with Homebrew. Run `doctor` again until it is clean.
3. **Ask the person, in one message:** printer model, nozzle size, plate type, what filament is on each head, whether the
   printer runs Klipper/Moonraker and where it is on the network, and whether they use Spoolman. Never guess these.
4. **A slicer setup for their printer.** `python3 tools/orca.py printers` lists what exists. If theirs is missing, build one
   with `orca.py new-printer` from the slicer's vendor profiles, then `orca.py adopt-gcode <a print that came out right>.gcode`
   so start, end and tool-change G-code match what already works.
5. **Klipper printers only: Workshop and the printer tools.** Run `python3 tools/workshop.py --printer <address>` on a machine
   next to the printer (home network or VPN only, never the open internet), then add the `printer` MCP server with the
   person's own name (`WORKSHOP_USER`), so every action is logged as theirs. Setup lines are in the README.

## 2. Prove it works (the first job)

Slice the four-colour test card for their printer and show the time and grams:
`python3 tools/orca.py slice examples/models/test-card.scad --printer <setup> --preset standard`.
Then build the Print Bench page (`tools/print_bench.py`) and send them its path. Only print it if they ask and say the bed is clear.

## 3. Rules you keep, whatever you are asked

- **A print starts only on the person's word that the bed is clear AND a fresh camera picture showing an empty plate.**
  Never on the picture alone, never on a guess, never while nobody is home. `printer_start` and `printer.py start` enforce
  `bed_clear`; do not work around them.
- **Never send motion, heat or a button press to test something** (homing, unload, a slicer's "send" button). Read the code
  or mock it.
- **Never guess a fit.** Anything that clips onto, slides into or screws to a real object needs a caliper number; mark it
  `FIT` and ask.
- **A Continue after the printer's own spaghetti-detector pause is the person's call.** Look at a fresh picture, ask, and only
  then resume (`person_checked`).
- **Profiles change through the person.** Calibration answers become a pending profile change for them to approve; do not
  write a live slicer profile behind their back.
- **Log as the person.** Actions go through Workshop with their name, not "Claude".
- **Check a downloaded model's licence** before sharing a remix (many are CC BY-NC).

## 4. Which skill for which job

| The person says | Skill |
| --- | --- |
| "design me a...", "here's an STL, add...", "make this two colours" | `print-workflow` (and `3d-print-designer` for the design rules) |
| "it's stringing", "the first layer won't stick", "the prime tower fell" | `print-troubleshooting` |
| "tune this filament", "flow", "pressure advance", "temperature" | `print-calibration`: numbered pieces the person picks by eye, no measuring |
| "dry this spool", "is it wet", "how much is left" | `filament-care` |
| "get the supports off", "paint it", "glue these", "put inserts in" | `print-postprocess` |
| "clean the plate", "grease the rods", "what maintenance is due" | `print-maintenance` |
| "a board for...", "a PCB", "a case for this board" | `electronics-design`, `pcb-layout`, `enclosure-from-pcb` |

Presets for `orca.py`: `draft`, `standard`, `strong`, `solid`, `fine`, `quality`, `figure` (figurines: 0.16 mm and a gentle
outer wall), `smooth`, `polished`, `lean`, `low-waste`, `grip`, `fine-text`. `python3 tools/orca.py slice --help` lists them.

## 5. Things that catch people out

- **Painted 3MFs from MakerWorld or another printer** flatten to one colour in the slicer CLI. Workshop's 3MF import (or the
  steps in `print-workflow`) rebuilds them with the paint remapped to the loaded heads.
- **Multicolour prime towers** snap when a different material is purged into them and lift off a dirty plate; see
  `print-troubleshooting` section I.
- **Wet PETG** strings and blobs whatever the settings: dry it first.
- **A low camera** only shows large failures. Say so rather than calling a print fine from it.
- **Snapmaker U1:** read [snapmaker-u1.md](snapmaker-u1.md) before the first print.

## 6. Done means

The tools pass `doctor`, the person's printer has a working slicer setup, the test card slices with real time and grams,
and, on a Klipper printer, Workshop answers and the printer tools show the person's name in the activity log.
