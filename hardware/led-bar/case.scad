// parts: panel diffuser board
// 1U faceplate for the rack status LED bar, for a 10-inch rack (254 mm wide, 44.45 mm tall). Prints face down: the
// black panel with the white diffuser printed into its window in the same print. The board hangs behind it on four
// standoffs with its LEDs facing the window. Every board figure comes from board_dims.scad (tools/pcb.py dims).
//   part = "panel"     black: plate, rack ear slots, standoffs
//   part = "diffuser"  white: fills the window, 0.8 mm thick at the front
//   part = "board"     the PCB from KiCad, placed where it mounts (for the Print Bench only, not printed)
//   part = "all"       preview
include <board_dims.scad>
part = "all";
$fn = 40;

U = 44.45; W = 254; plate = 3;               // 10-inch 1U panel
ear_x = 118.2625; ear_z = [6.35, 22.225, 38.1];   // EIA-310 hole positions for a 10-inch rack, from the panel centre
gap = board_tallest + 2;                     // standoff height: the tallest part on the board plus 2 mm
diff_t = 0.8;                                // diffuser thickness: test strip first (SPECS.md open question)
window_pad = 4;                              // window margin round the LED row
standoff_d = 6; pilot_d = 2.5;               // M3 self-tapping into PETG

// board position in panel coordinates: centred, seen from the front (x right, y up, z into the rack)
bx0 = (W + board_w) / 2;                     // the board's left edge (u = 0) as seen from the back is at the right from the front
by0 = (U + board_h) / 2;                     // the board's top edge
function px(u) = bx0 - u;
function py(v) = by0 - v;

led_x = [for (l = board_leds) px(l[0])];
led_y = py(board_leds[0][1]);
win = [min(led_x) - window_pad, led_y - 4, max(led_x) - min(led_x) + 2 * window_pad, 8];   // x, y, w, h

module ear_slots() for (x = [W / 2 - ear_x, W / 2 + ear_x], z = ear_z)
  translate([x, z, -1]) hull() for (dx = [-1.5, 1.5]) translate([dx, 0, 0]) cylinder(d = 6, h = plate + 2);
module window(h) translate([win[0], win[1], -0.01]) linear_extrude(h) offset(r = 1) offset(r = -1) square([win[2], win[3]]);

module panel() difference() {
  union() {
    hull() {   // chamfered front edge
      translate([0.8, 0.8, 0]) cube([W - 1.6, U - 1.6, 0.01]);
      translate([0, 0, 0.8]) cube([W, U, plate - 0.8]);
    }
    for (h = board_holes) translate([px(h[0]), py(h[1]), plate - 0.01]) cylinder(d = standoff_d, h = gap + 0.01);
  }
  ear_slots();
  window(plate + 1);
  for (h = board_holes) translate([px(h[0]), py(h[1]), plate + 1]) cylinder(d = pilot_d, h = gap + 1);
}
module diffuser() window(diff_t);

// KiCad's board STL: x 50..250, y -86..-50 (board top-left at x 50, y -50), z up from the board's back face.
// Turned over (top side towards the panel) and moved so its holes sit on the standoffs.
module board() translate([bx0 + 50, by0 + 50, plate + gap + board_t]) rotate([0, 180, 0]) import("fab/led-bar.stl", convexity = 4);

if (part == "panel" || part == "all") color("#1f1f22") panel();
if (part == "diffuser" || part == "all") color("#ecebe6") diffuser();
if (part == "board" || part == "all") color("#2d5a3d") board();
