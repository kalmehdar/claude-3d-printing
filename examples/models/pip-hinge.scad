// parts: card
// Print-in-place hinge ladder: four pin-and-barrel hinges printed flat and already assembled, each with a bigger gap.
// Turn each one after printing; the smallest gap that swings freely is your printer's print-in-place gap.
// Tell Claude that number; designs with a printed hinge, pivot or captive part use it instead of a guess.
// The gap is all the way round the pin (radial), and the ladder starts at your measured slide fit or 0.3 mm,
// whichever is bigger: under the pin the gap must be more than one layer (0.2 mm), or the pin's first layer prints
// straight onto the knuckle below and the hinge is solid.
// How it stays free:
//   - the pin belongs to the outer knuckles (leaf A); the middle knuckles (leaf B) have a hole round it, the gap all round
//   - the hole has a 45 degree teardrop roof, so its top bridges well clear of the pin instead of sagging onto it
//   - knuckle ends sit at least 0.3 mm apart along the axis (vertical walls print clean, so that gap can stay small)
//   - the hinge axis lies along the bed; the leaves are flat plates, so nothing needs supports
// Prints flat as it is, one colour. About 40 minutes and 16 g (OrcaSlicer, U1 PETG and P1S PLA).
// Then: python tools/fit.py set --slide <s> --press <p> --pip <the gap that swung freely>
//   part = "card"   the four hinges, gaps under each leaf  ("all" previews the same)
part = "card";
$fn = 64;
font = "Liberation Sans:style=Bold";

slide_fit = 0.3;                    // replace with the lines `python tools/fit.py scad` prints to use your measured fit
EXTRA = [0, 0.1, 0.2, 0.3];         // gap = max(slide_fit, 0.3) + EXTRA[i] for the four hinges
base = max(slide_fit, 0.3);
R = 3.8;                            // knuckle radius
rp = 1.8;                           // pin radius
t = 2.4;                            // leaf thickness
leaf = 12;                          // leaf width from the knuckles
L = 40;                             // hinge length
n = 5;                              // knuckles (odd: leaf A has both ends, so the pin's ends are buried)
gap_min = 0.3;                      // knuckle end to knuckle end
deboss = 0.6;

function hole_r(c) = rp + c;
function axial(c) = max(gap_min, c);
function knuckle_len(c) = (L - (n - 1) * axial(c)) / n;

// a hole along x with its point upwards: rotate([0, 90, 0]) sends the 2D -x direction to +z
module teardrop_x(r, h) rotate([0, 90, 0]) linear_extrude(h, center = true)
  hull() { circle(r); translate([-r * sqrt(2), 0]) circle(0.01); }

module knuckle(x0, k) translate([x0, 0, R]) rotate([0, 90, 0]) cylinder(r = R, h = k);

module hinge(c) {
  k = knuckle_len(c);
  edge = R + axial(c);                                     // a leaf stops this far from the axis
  for (i = [0 : n - 1]) let(x0 = -L / 2 + i * (k + axial(c)), a = i % 2 == 0) {
    if (a) {
      knuckle(x0, k);
      translate([x0, -edge - 0.01, 0]) cube([k, edge + 0.01, t]);                 // web to leaf A
    } else difference() {
      union() { knuckle(x0, k); translate([x0, 0, 0]) cube([k, edge + 0.01, t]); }   // web to leaf B
      translate([x0 + k / 2, 0, R]) teardrop_x(hole_r(c), k + 2);
    }
  }
  translate([-L / 2, 0, R]) rotate([0, 90, 0]) cylinder(r = rp, h = L);           // the pin, part of leaf A's knuckles
  translate([-L / 2, -edge - leaf, 0]) cube([L, leaf, t]);                          // leaf A
  difference() {
    translate([-L / 2, edge, 0]) cube([L, leaf, t]);                                // leaf B, its gap pressed in
    translate([0, edge + leaf / 2, t - deboss]) linear_extrude(deboss + 0.01)
      text(str(c), size = 5, font = font, halign = "center", valign = "center");
  }
}

pitch = 2 * (R + gap_min + leaf) + 2 * max(EXTRA) + 6;
module card() for (i = [0 : len(EXTRA) - 1]) translate([0, i * pitch, 0]) hinge(base + EXTRA[i]);

for (e = EXTRA) let(c = base + e) {
  assert(hole_r(c) < R - 1.2, str("gap ", c, ": the knuckle wall round the hole would be under 1.2 mm"));
  assert(knuckle_len(c) > 4, "knuckles too short to hold the pin");
  assert(c >= 0.3, "under the pin the gap must be more than one 0.2 mm layer");
}
echo(gaps = [for (e = EXTRA) base + e], knuckle_mm = knuckle_len(base), wall_mm = R - hole_r(base + max(EXTRA)));
if (part == "card" || part == "all") color("#1f1f22") card();
