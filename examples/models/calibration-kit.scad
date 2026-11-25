// parts: black white
// Calibration kit: tune a filament by picking a number, not by measuring (about 1 h 30 min for the lot, 25 g).
// Three numbered tests and a tool-changer check, laid out to look at. To run them for real, ask Claude to calibrate a
// filament: tools/caltest.py (or Workshop's one-tap tests) builds each test on the right head and writes the per-piece
// commands (SET_PRESSURE_ADVANCE, M221, M104) into the G-code. Then tell Claude one number per test.
//   Pressure advance: 8 chevrons. Pick the crispest V tip (no bulge before the point, no dip after it).
//   Flow: 9 tiles. Pick the smoothest top (lines flat and just touching).
//   Temperature: a tower in 5 bands, hottest at the bottom. Pick the band with the cleanest bridge and least string.
//   No-tower check (tool changers): half black, half white, a head swap on every layer with no prime tower. Look for
//   black specks on the white half and gaps where each colour starts.
part = "black";
$fn = 48;

module label(t, s=4.6, h=0.6) linear_extrude(h) text(t, size=s, font="Liberation Sans:style=Bold", halign="center", valign="center");

// pressure advance: a 90 degree chevron on a numbered tile
module chevron(n) {
  cube([24, 10, 0.6]);
  translate([12, 5, 0.6]) label(str(n));
  translate([12, 12, 0]) linear_extrude(10) polygon([[0,0],[11,11],[8.9,13.1],[0,4.24],[-8.9,13.1],[-11,11]]);
  translate([1, 10, 0]) cube([22, 13, 0.6]);
}
// flow: an 18 mm tile with its number on a tab in front
module tile(n) {
  translate([0, 7, 0]) cube([18, 18, 1.6]);
  cube([18, 6.5, 0.6]);
  translate([9, 3.25, 0.6]) label(str(n), 4);
}
// temperature: 5 bands of 6 mm, each with a 10 mm bridge, a 45 degree overhang and its temperature on the front
temps = ["255","250","245","240","235"];
module tower() {
  for (i=[0:4]) translate([0,0,i*6]) {
    difference(){ cube([20,10,6]); translate([5,-1,0.6]) cube([10,12,3.2]); }
    translate([20,0,0]) hull(){ cube([0.01,10,0.6]); translate([0,0,5.4]) cube([5,10,0.6]); }
    translate([10,0.01,4.9]) rotate([90,0,0]) label(temps[i], 1.8);
  }
}

if (part == "black" || part == "all") color("#1f1f22") {
  for (i=[1:8]) translate([(i-1)*28, 0, 0]) chevron(i);
  translate([0, 86, 0]) cube([15, 30, 15]);                 // no-tower check, black half
}
if (part == "white" || part == "all") color("#ecebe6") {
  for (i=[1:9]) translate([(i-1)*24, 36, 0]) tile(i);
  translate([60, 86, 0]) tower();
  translate([15, 86, 0]) cube([15, 30, 15]);                // no-tower check, white half
}
