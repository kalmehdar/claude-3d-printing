# Add your own design

Designs made with Claude are welcome: useful household and workshop parts, Gridfinity, cable management, multi-colour tricks, and upgrades of existing STLs whose licence allows remixes.

## What a design needs

1. **The OpenSCAD source** in `examples/models/<name>.scad`:
   - every size as a named variable at the top, with a comment saying what it is
   - `// parts: a b c` on the first line, one part per colour, selected with `part = "..."`
   - anything that has to fit something real marked `// FIT:` with what to measure
   - for an upgrade, the original STL in `examples/before/` with its source and licence in a comment
2. **The exported parts** in `examples/stl/`:

   ```bash
   cd examples
   python ../tools/export_parts.py models/<name>.scad --out stl --bed 256x256x256
   ```

3. **A clean mesh check.** It must end `0 file(s) need a fix`:

   ```bash
   blender --background --factory-startup --python ../tools/mesh_audit.py -- stl --bed 256
   ```

4. **An entry in `examples/bench.json`**: id, name, what it is for, status, material and heads, credit, the `source` path, and its parts with their filament colours.
5. **A picture**, rendered the same way as the others:

   ```bash
   blender --background --factory-startup --python ../tools/render.py -- bench.json renders --only <id>
   ```

   Save it as `docs/images/<id>.jpg`, about 1200 x 750.

Then build the bench locally (`python ../tools/print_bench.py bench.json print-bench.html`), open it, turn your design round and measure one critical size. Open a pull request saying what you printed it on and how it came out. A photo of the real print helps.

## Rules

- Only designs you made, or remixes the original licence allows. Credit the original author and link it.
- No weapons or weapon parts, and nothing meant to defeat a lock, a safety device or a law.
- New tools or changes to the existing ones: keep them standard-library Python (plus Blender's own `bpy` for the Blender tools), and run them on the examples before sending.
