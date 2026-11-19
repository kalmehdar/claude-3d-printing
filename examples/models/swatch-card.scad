// parts: body text
// Filament swatch card: one per spool. The name and the temperatures it printed well at are printed in, and a strip
// of steps 0.4 / 0.8 / 1.2 / 1.6 mm thick along the bottom shows how see-through the colour is at each thickness.
// Two heads: card in the spool's colour, text in a contrasting one (swap them for a white or pale spool).
// A hole in the corner puts the set on a ring.
//   part = "body" | "text";  part = "all" preview
part = "all";
$fn = 48;
font = "Liberation Sans:style=Bold";
inlay = 0.6;                   // text depth

swatch_name = "PETG BLACK";    // set per spool
nozzle = 255; bed = 80;        // the temperatures from the filament profile it printed well with
printer = "";                  // optional, bottom right, e.g. "A1"
card = [76, 40, 2.4]; r = 4; hole_d = 5;
steps = [0.4, 0.8, 1.2, 1.6];  // the see-through strip
step_w = 12;

module card_outline() offset(r) offset(-r) square([card[0], card[1]]);
module body() difference() {
  linear_extrude(card[2]) card_outline();
  translate([card[0] - 7, card[1] - 7, -1]) cylinder(d = hole_d, h = card[2] + 2);                        // ring hole
  for (i = [0 : len(steps) - 1]) translate([4 + i * step_w, 3, steps[i]]) cube([step_w, 10, card[2]]);   // the steps
  text_shape(inlay + 0.01);
}
module text_shape(h = inlay) translate([0, 0, card[2] - h + 0.01]) linear_extrude(h) {
  translate([5, 31]) text(swatch_name, size = 5.2, font = font);
  translate([5, 22.5]) text(str(nozzle, "°C  BED ", bed, "°C"), size = 3.6, font = font);
  for (i = [0 : len(steps) - 1]) translate([4 + i * step_w + step_w / 2, 15]) text(str(steps[i]), size = 2.2, font = font, halign = "center");
  if (printer != "") translate([card[0] - 5, 3.8]) text(printer, size = 4, font = font, halign = "right");
}

if (part == "body" || part == "all") color("#1f1f22") body();
if (part == "text" || part == "all") color("#ecebe6") text_shape();
