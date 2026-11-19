// Desk cable clip: a cable snaps into the ring, a 20 x 20 mm VHB pad holds the flat base to the desk edge.
// One colour, prints flat on its base with no supports (the ring is a short bridge).
// FIT: measure your cable's outside diameter and set cable_d; the ring adds 0.3 mm of play and the mouth is 70% of
// the cable so it snaps past and stays.
$fn = 48;
cable_d = 6.0;   // FIT: cable outside diameter (Cat6 is about 6 mm, a USB lead about 4)
clip_w = 10;     // width along the cable
pad = 20;        // base length, to suit a 20 x 20 mm adhesive pad
base_t = 2;

difference() {
  union() {
    translate([-pad / 2, -clip_w / 2, 0]) cube([pad, clip_w, base_t]);                                             // base
    translate([0, clip_w / 2, base_t + cable_d / 2]) rotate([90, 0, 0]) cylinder(d = cable_d + 3.2, h = clip_w);    // ring
  }
  translate([0, clip_w / 2 + 1, base_t + cable_d / 2]) rotate([90, 0, 0]) cylinder(d = cable_d + 0.3, h = clip_w + 2);
  translate([-cable_d * 0.35, -clip_w / 2 - 1, base_t + cable_d * 0.75]) cube([cable_d * 0.7, clip_w + 2, cable_d]);   // snap-in mouth
}
