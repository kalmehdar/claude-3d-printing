// Smooth-wall test: 30 mm tall, round on one side and flat on the other, so layer lines show on both kinds of wall.
// Raking light along the flat face shows banding best.
$fn = 160;
intersection() {
    cylinder(d = 30, h = 30);
    translate([-15, -9, 0]) cube([30, 24, 30]);
}
