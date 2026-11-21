// parts: card
// Hinge card: three print-in-place hinges, printed flat in one go, to find the gap your printer needs.
// Each hinge is two leaves joined by interlocking cones (BOSL2 knuckle_hinge, in_place=true), with the
// gap printed on its leaf. After printing, flex each hinge gently until it frees up. The right gap is
// the smallest one that frees with a little force and then swings without rubbing; a fused hinge means
// that gap is too small. Store it with `python tools/fit.py set --slide <mm> --press <mm> --hinge <mm>`
// and later hinged designs read it as hinge_gap. Needs BOSL2 (github.com/BelfrySCAD/BOSL2) in OpenSCAD's library folder.
// Lay-flat rule: the hinge axis runs along the bed, so the cones print as 45 degree overhangs, not bridges.
include <BOSL2/std.scad>
include <BOSL2/hinges.scad>

part = "card";   // one part only; kept for the export tool
$fn = 48;
font = "Liberation Sans:style=Bold";

GAPS = [0.2, 0.3, 0.4];
t = 5;                  // leaf thickness = barrel diameter, so the whole card prints flat
leaf = 16;              // leaf width (X), each side of the hinge
len_y = 30;             // hinge length (Y)
offset = 3;             // pin centre from each leaf edge: barrel radius 2.5 + 0.5 clearance to the other leaf
pitch = 40;             // hinge to hinge (Y)
segs = 5;
deboss = 0.6;

module hinge(g) {
  // left leaf, outer knuckles on its right edge
  cuboid([leaf, len_y + 2, t], anchor = BOT + RIGHT)
    position(RIGHT) orient(anchor = RIGHT)
      knuckle_hinge(length = len_y, segs = segs, offset = offset, arm_height = 0, arm_angle = 90,
                    clear_top = true, knuckle_diam = t, in_place = true, gap = g);
  // right leaf, inner knuckles on its left edge; the pins meet at x = offset
  right(2 * offset) cuboid([leaf, len_y + 2, t], anchor = BOT + LEFT)
    position(LEFT) orient(anchor = LEFT)
      knuckle_hinge(length = len_y, segs = segs, offset = offset, arm_height = 0, arm_angle = 90,
                    clear_top = true, knuckle_diam = t, in_place = true, gap = g, inner = true);
}

module card() for (i = [0 : len(GAPS) - 1]) back(i * pitch) difference() {
  hinge(GAPS[i]);
  translate([-leaf / 2, 0, t - deboss]) linear_extrude(deboss + 0.01)
    text(str(GAPS[i]), size = 5, font = font, halign = "center", valign = "center");
}

assert(t / 2 + 0.3 <= offset, "barrel would touch the other leaf");
color("#1f1f22") card();
