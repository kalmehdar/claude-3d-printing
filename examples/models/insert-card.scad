// parts: card
// Insert card: find the hole your printer and material need for M3 heat-set inserts, in one short print.
// Six bosses, each with a pocket drawn at the diameter printed in front of it. Melt an insert into each one
// (iron 10-20 C over the print temperature, stop about 90% in, press the last bit flush with a flat tool),
// let it cool, then screw an M3 bolt in and out of each. The best hole is the smallest one where:
//   the insert went in straight and sits flush, no plastic welled up over it, and it does not turn with the bolt.
// Pull-out strength drops sharply once the hole is too big (CNC Kitchen, "Are Our Heat-Set Insert Datasheets Wrong?"),
// so when two holes both pass, pick the smaller. Tell Claude the number:
//   python tools/fit.py set --slide <mm> --press <mm> --insert <mm>
// and every later design uses it for M3 inserts.
part = "card";   // one part only; kept for the export tool
$fn = 96;
font = "Liberation Sans:style=Bold";

HOLES = [3.8, 3.9, 4.0, 4.1, 4.2, 4.3];
insert_len = 5.7;                      // M3 x 5.7 is the common short insert; set yours from the bag
pitch = 13;                            // boss centre to centre
boss_d = 8.6;                          // about 2x the hole, as the fasteners pattern asks
plate = 2;                             // base plate under the bosses
boss_h = insert_len + 1 + 1;           // pocket insert_len + 1 deep, 1 mm of floor under it
w = pitch * len(HOLES) + 4; h = 24;
deboss = 0.6;
chamfer = 0.4;                         // lead-in so the insert starts square

module card() difference() {
  union() {
    cube([w, h, plate]);
    for (i = [0 : len(HOLES) - 1]) translate([2 + pitch / 2 + i * pitch, 15, 0]) cylinder(d = boss_d, h = plate + boss_h);
  }
  for (i = [0 : len(HOLES) - 1]) {
    x = 2 + pitch / 2 + i * pitch;
    top = plate + boss_h;
    translate([x, 15, top - insert_len - 1]) cylinder(d = HOLES[i], h = insert_len + 1 + 0.01);
    translate([x, 15, top - chamfer + 0.01]) cylinder(d1 = HOLES[i], d2 = HOLES[i] + 2 * chamfer, h = chamfer);
    translate([x, 4.5, plate - deboss]) linear_extrude(deboss + 0.01)
      text(HOLES[i] == floor(HOLES[i]) ? str(HOLES[i], ".0") : str(HOLES[i]), size = 3.2, font = font, halign = "center", valign = "center");
  }
}

assert(len(HOLES) == 6);
assert(boss_d >= 1.75 * max(HOLES), "boss wall too thin for the largest hole");
color("#1f1f22") card();
