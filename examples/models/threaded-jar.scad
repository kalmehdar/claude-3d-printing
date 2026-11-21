// parts: jar lid
// A screw-top jar and lid that print without supports and screw together first time.
//   - 90 degree trapezoidal thread: each flank is 45 degrees, so the underside of every turn prints as an ordinary
//     45 degree overhang (a 60 degree V thread leaves its flanks 30 degrees off horizontal: a steep overhang)
//   - the clearance is your measured slide fit: BOSL2 adds 4 * $slop to an internal thread's diameter, so
//     $slop = slide_fit / 4 gives exactly that much play on the diameter
//   - blunt starts and a lead-in on both threads, so the lid finds the thread instead of cross-threading
//   - the jar prints upright, the lid prints top-down (its top on the bed), both flat on the plate
// Defaults make a small test jar (about 40 mm across, an hour-ish); set inner_d and inner_h for a real one.
// Needs BOSL2 (github.com/BelfrySCAD/BOSL2) in OpenSCAD's library folder.
//   part = "jar" | "lid"   one part
//   part = "all"           both, as they print, side by side
include <BOSL2/std.scad>
include <BOSL2/threading.scad>

part = "all";
$fn = 96;

slide_fit = 0.3;            // replace with the lines `python tools/fit.py scad` prints
inner_d = 30;               // inside diameter of the jar
inner_h = 25;               // inside height, floor to the top of the thread
wall = 2;
floor_t = 1.6;
pitch = 4;                  // one turn every 4 mm
thread_len = 9;             // length of thread on the jar's neck
lid_top = 1.6;

thread_angle = 90;          // included angle: 45 degree flanks
depth = pitch / 2 * 0.6;    // thread depth, kept shallow so the walls stay thick
thread_d = inner_d + 2 * wall + 2 * depth;   // major diameter of the jar's thread
lid_d = thread_d + 2 * wall;
lid_h = thread_len + lid_top;
$slop = slide_fit / 4;

module jar() {
  body_h = floor_t + inner_h - thread_len;
  difference() {
    union() {
      cyl(d = thread_d - 2 * depth, h = body_h, anchor = BOTTOM, rounding1 = 1);              // the body below the thread
      up(body_h) trapezoidal_threaded_rod(d = thread_d, l = thread_len, pitch = pitch, thread_angle = thread_angle,
                                          thread_depth = depth, blunt_start = true,
                                          bevel2 = false, anchor = BOTTOM);
    }
    up(floor_t) cyl(d = inner_d, h = inner_h + 1, anchor = BOTTOM);                            // the inside
  }
}

module lid() difference() {                       // drawn as it prints: top on the bed, opening upwards
  cyl(d = lid_d, h = lid_h, anchor = BOTTOM, rounding1 = 1);
  up(lid_top) trapezoidal_threaded_rod(d = thread_d, l = thread_len + 1, pitch = pitch, thread_angle = thread_angle,
                                       thread_depth = depth, internal = true, blunt_start = true,
                                       bevel1 = false, anchor = BOTTOM);
}

assert(thread_len >= 2 * pitch, "at least two full turns of thread, or the lid wobbles and pulls off");
assert(wall >= 1.6, "walls under 1.6 mm crack along the layers where the thread loads them");
assert(depth >= 0.8, "a thread shallower than 0.8 mm strips: raise the pitch");
echo(thread_d = thread_d, lid_d = lid_d, clearance_on_diameter = 4 * $slop, jar_h = floor_t + inner_h);

if (part == "jar" || part == "all") color("#1f1f22") jar();
if (part == "lid" || part == "all") color("#c8102e") right(lid_d / 2 + thread_d / 2 + 6) lid();
