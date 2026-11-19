---
name: print-workflow
description: 'Takes a 3D print from idea or existing STL to a checked, multi-colour, previewed set of print files using the claude-3d-printing tools: OpenSCAD per-colour parts, export with a bed check, a Blender mesh audit, and a Print Bench page with sizes and a measure tool. Use when someone says "design me a ...", "make this print in two colours", "add a label to this STL", "upgrade this print", "remix this", "show me the prints", "build a print bench", "check this STL", or hands over an STL to improve.'
---

# Print workflow

The loop: **ask, model, export, audit, preview, hand over.** Design rules (wall thickness, overhangs, tolerances, materials) come from the `3d-print-designer` skill; this skill is the pipeline around them and the house style that makes the result look finished.

Tools live in a clone of github.com/kalmehdar/claude-3d-printing (`tools/`). Find it before starting; if there is none, clone it.

## 1. Ask (one message, then stop)

- The printer and its bed size, the nozzle, how many colours it can print and the material. Skip what the user already said.
- What the part must fit. Anything that clips on, slides in, screws to or holds something real needs a caliper number from the person. Never guess a fit size: write it as a named variable marked `// FIT: measure ...` and say so.
- For an upgrade: the STL, what is wrong with it or what to add, and its licence (remixes of CC BY-NC models are personal use only; credit the original).

## 2. Model (OpenSCAD, one file)

- Every size a named variable at the top, `$fn = 48`, comments saying what each number is.
- One `part` variable selects a colour, and the first line lists them: `// parts: body label accent`. Add `part = "all"` for a preview with `color()` on each part.
- Labels are printed in, never stuck on: a 0.6 mm pocket in the body, the same text clipped to the part as the second colour, printed face down (so mirror the text on a bottom face).
- Same-colour shapes that touch only along an edge or a corner make non-manifold meshes: overlap them by 0.01 mm.
- Upgrading an STL: `import("file.stl", convexity = 6)`, then `difference()` to cut (pockets, holes, label recesses), `union()` to add (hooks, tabs, feet, ribs), `intersection()` to lift a region out as its own colour, removing it from the body with `difference()`. Read the STL's size and bounds first (run `export_parts.py` on a file that imports it, or `mesh_audit.py` on its folder) and place everything from those numbers. `examples/models/upgrade-bin.scad` is the pattern.

## 3. Export

```bash
python tools/export_parts.py model.scad --out stl --bed 256x256x256
```

One binary STL per part, each checked against the bed. Set `OPENSCAD` if it is not on PATH; a development snapshot uses the manifold backend and is much faster. A part too big for the bed: split it with `intersection()` and add alignment pins or a dovetail at the joint.

## 4. Audit

```bash
blender --background --factory-startup --python tools/mesh_audit.py -- stl --bed 256
```

It must end `0 file(s) need a fix`. A solid body should be `1` piece; lettering is many by design. A large thin-area number on a solid part means look at that spot in the slicer. No Blender: say the audit was skipped, never that the mesh is fine.

## 5. Preview: the Print Bench

Add the design to a `bench.json` (format in `tools/print_bench.py`) with its parts in their real filament colours, then:

```bash
python tools/print_bench.py bench.json print-bench.html
```

Look at it yourself before showing it: the list picture, the colours, the size lines (width, depth, height in mm), and use Measure to check one critical distance against the spec. For an upgrade, list the original as "before" next to it. To share it publicly, put it on GitHub Pages (the repo's `pages.yml` workflow does this on every push to main); a Claude artifact link only opens inside the owner's account.

Use these colours so every bench looks like one set: black `#1f1f22`, white `#ecebe6`, red `#c8102e`, blue `#002fa7`, grey `#8a8f98`. Use the real filament colour where it differs.

## 6. Hand over

- Which file goes on which head, the orientation (label faces down on the plate), supports or none and why, and anything still marked FIT.
- Slice in the printer maker's slicer with its own profile; its time and grams beat the bench's estimate (about 20% either way).
- The person starts the print and watches the first layer. Never start a print for someone who has not said the bed is clear and they are there.

## Done means

Exported parts within the bed, the audit at 0 to fix, the bench rebuilt and looked at, and every FIT number either measured or listed for the person to measure.
