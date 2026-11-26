// Seam test cylinder (26 Nov 2026): 22 mm across, 20 mm tall, its number cut 0.6 mm into the top so each variant is told
// apart after printing. Four of these go on one plate with different per-object seam settings (see build.py).
n = 1;
$fn = 120;
difference() {
  cylinder(d = 22, h = 20);
  translate([0, 0, 19.4]) linear_extrude(1) text(str(n), size = 10, font = "Liberation Sans:style=Bold", halign = "center", valign = "center");
}
