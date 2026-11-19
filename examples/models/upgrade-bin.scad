// parts: body rim label
// Upgrade a print you already have, from its STL alone: no source file needed. The STL here is a plain 2x1
// Gridfinity bin standing in for something downloaded. Three upgrades, each one a few lines:
//   1. a label printed into the front (pocket in the body, letters in a second colour)
//   2. an existing feature recoloured: the top 3 mm of the rim cut off and printed in the accent colour
//   3. a fit change: 6 x 2 mm magnet pockets in the base, one per grid cell
// The same pattern works on any STL: import() it, then difference() to cut, union() to add,
// intersection() to pull out a region for another colour. Sizes come from the STL itself
// (export_parts.py or mesh_audit.py print them); positions below are for this bin, centred on the origin.
//   part = "body" | "rim" | "label"   one colour each;  part = "all" preview
part = "all";
$fn = 48;
src = "../before/plain-bin-2x1.stl";
size = [83.5, 41.5, 42.0];        // from the STL: width, depth, height (mm)
font = "Liberation Sans:style=Bold";
inlay = 0.6;                      // label depth
label = "M3 SCREWS"; label_z = 26; label_size = 6;
rim_h = 3;                        // accent band at the top
magnet = [6.2, 2.2];              // pocket diameter and depth for 6 x 2 mm magnets (+0.2 clearance)
magnet_at = [[-21, 0], [21, 0]];  // one per 42 mm grid cell

module original() import(src, convexity = 6);
module label_shape(h) translate([0, -size[1] / 2 - 0.01, label_z]) rotate([90, 0, 0]) mirror([0, 0, 1])
  linear_extrude(h) text(label, size = label_size, font = font, halign = "center", valign = "center");
module rim_zone() translate([-size[0], -size[1], size[2] - rim_h]) cube([2 * size[0], 2 * size[1], rim_h + 1]);

module body() difference() {
  original();
  rim_zone();                                                     // 2: the rim becomes its own part
  label_shape(inlay + 0.01);                                      // 1: the label pocket
  for (p = magnet_at) translate([p[0], p[1], -0.01]) cylinder(d = magnet[0], h = magnet[1] + 0.01);   // 3
}
module rim() intersection() { original(); rim_zone(); }
module label_part() intersection() { label_shape(inlay); original(); }

if (part == "body" || part == "all") color("#1f1f22") body();
if (part == "rim" || part == "all") color("#c8102e") rim();
if (part == "label" || part == "all") color("#ecebe6") label_part();
