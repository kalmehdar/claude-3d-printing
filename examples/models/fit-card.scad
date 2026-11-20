// parts: card peg
// Fit card: find your printer's real clearances in one short print (about 20 minutes, 6 g), instead of guessing from the material.
// Six holes, each 8 mm plus the clearance printed under it, and an 8 mm peg printed beside them. Try the peg in every
// hole, then tell Claude two numbers:
//   slide: the smallest hole the peg turns in freely      (hinges, lids, sliding parts)
//   press: the smallest hole the peg goes into with a firm push and stays   (press fits, pins)
// Claude stores them with `python tools/fit.py set --slide <mm> --press <mm>` and every later design uses them.
// Clearances are on the diameter, the way the designs use them: hole = shaft + clearance.
//   part = "card" | "peg"   one part
//   part = "all"            preview, as they sit on the plate
part = "all";
$fn = 96;                               // round holes: facets would make the fit tighter than the number says
font = "Liberation Sans:style=Bold";

CLEAR = [0.05, 0.10, 0.15, 0.20, 0.30, 0.40];
d = 8;                                  // peg diameter, and the base of every hole
pitch = 13;                             // hole centre to centre
t = 4;                                  // card thickness: enough wall for the peg to bind against
w = pitch * len(CLEAR) + 4; h = 26;
chamfer = 0.4;                          // both ends of every hole, so the first layer's squish doesn't close it
deboss = 0.6;

module card() difference() {
  cube([w, h, t]);
  for (i = [0 : len(CLEAR) - 1]) {
    x = 2 + pitch / 2 + i * pitch;
    dh = d + CLEAR[i];
    translate([x, 16, -1]) cylinder(d = dh, h = t + 2);
    translate([x, 16, -0.01]) cylinder(d1 = dh + 2 * chamfer, d2 = dh, h = chamfer);
    translate([x, 16, t - chamfer + 0.01]) cylinder(d1 = dh, d2 = dh + 2 * chamfer, h = chamfer);
    translate([x, 5, t - deboss]) linear_extrude(deboss + 0.01)
      text(str(CLEAR[i]), size = 3.4, font = font, halign = "center", valign = "center");
  }
}

module peg() translate([w + 10, 16, 0]) {   // stands upright: the round side is the one that has to be true
  cylinder(d1 = d - 2 * chamfer, d2 = d, h = chamfer);
  translate([0, 0, chamfer]) cylinder(d = d, h = 12 - chamfer);
  translate([-6, -1.5, 0]) cube([12, 3, 2]);   // a foot to hold it by and to keep it standing on the plate
}

assert(len(CLEAR) == 6);
if (part == "card" || part == "all") color("#1f1f22") card();
if (part == "peg" || part == "all") color("#3a3b40") peg();
