// parts: card
// Snap fits and a living hinge that check their own strain: a two-arm clip that clicks through a slot in a plate,
// and a living-hinge sample. Print the card, push the clip through the plate's slot (it clicks, and comes out
// with a firm pull because the return face is 45 degrees), and fold the hinge a few times.
// The numbers come from the 3d-print-designer skill (functional-parts.md):
//   strain at the arm root  e = 1.5 * t * y / L^2      (t thickness, y hook depth = deflection, L root to hook)
//   design strain           about 2% for PLA, about 3% for PETG
//   arms print flat and bend within the layer, never across it
//   living hinge            0.3 to 0.8 mm thick, at least 5 mm long, PETG/PP/nylon (PLA cracks in a few hundred folds)
// The asserts refuse a design that breaks these, so a wrong size stops here and not as a snapped arm.
// Reuse: use <snap-fit.scad>, then snap_clip(...), snap_slot(...) and living_hinge(...).
//   part = "card"   clip, plate and hinge sample on one plate ("all" previews the same)
part = "card";
$fn = 48;

material = "PETG";                  // PLA or PETG; any other material needs strain_limit set by hand
strain_limit = material == "PLA" ? 0.02 : material == "PETG" ? 0.03 : undef;
slide_fit = 0.3;                    // replace with the lines `python tools/fit.py scad` prints

// clip arm
t = 1.6;            // arm thickness at the root (bending direction)
w = 6;              // arm width (the part's height on the bed)
y = 1.0;            // hook depth: how far each arm bends while going through
lead = 30;          // lead-in angle, degrees: lower pushes in more easily
ret = 45;           // return angle: 45 comes out with a firm pull, 90 never comes out
P = 3;              // plate thickness the clip goes through
S = 12;             // outer span of the two arms (the slot is this plus slide_fit)
base = 4;           // the clip's head behind the arm roots
play = 0.2;         // axial play so the hook clears the plate's back face
relief = 0.6;       // gap each side of an arm where it runs back into the head, so it flexes over its whole length
margin = 0.85;      // the arm is sized to this share of the strain limit

function strain(t, y, L) = 1.5 * t * y / (L * L);

// x = 0 is the shoulder that stops against the plate; the arm roots at x = -R, inside the head, so its flexing
// length (root to hook) is long enough for the strain limit even through a thin plate. y outward from the arm's face.
module hook_arm(R, L, t, w, y, lead, ret, x_r) {
  x_c = x_r + (ret >= 89.9 ? 0.01 : y / tan(ret));            // top of the return face
  x_f = x_c + 0.6;                                             // a short flat
  translate([-R - 1, -t, 0]) cube([R + 1 + L, t, w]);                      // 1 mm into the head, so arm and head are one body
  linear_extrude(w) polygon([[x_r, 0], [x_c, y], [x_f, y], [L, 0]]);
}

module snap_clip(t = t, w = w, y = y, lead = lead, ret = ret, P = P, S = S, base = base, play = play) {
  x_r = P + play;
  x_c = x_r + (ret >= 89.9 ? 0 : y / tan(ret));
  L = x_c + 0.6 + y / tan(lead);
  R = max(1, sqrt(1.5 * t * y / (margin * strain_limit)) - x_c);   // root this far behind the shoulder
  e = strain(t, y, R + x_c);
  assert(strain_limit != undef, str("no design strain known for ", material, ": set strain_limit"));
  assert(e <= strain_limit, str("arm strain ", round(e * 1000) / 10, "% is over ", strain_limit * 100,
                                "% for ", material, ": make the arm longer or thinner, or the hook shallower"));
  assert(S - 2 * t >= 2 * y + 0.5, "the arms would hit each other before the hooks clear the slot");
  assert(relief > slide_fit / 2, "the shoulders must sit outside the slot to stop against the plate");
  echo(clip_strain_pct = round(e * 1000) / 10, limit_pct = strain_limit * 100, flexing_length = R + x_c);
  translate([-R - base, -(S + 6) / 2, 0]) cube([base, S + 6, w]);                        // the head, where the arms root
  for (m = [0, 1]) mirror([0, m, 0]) {
    translate([0, S / 2, 0]) hook_arm(R, L, t, w, y, lead, ret, x_r);
    translate([-R - 0.01, S / 2 + relief, 0]) cube([R + 0.01, (S + 6) / 2 - S / 2 - relief, w]);   // shoulder outside the arm
    translate([-R - 0.01, 0, 0]) cube([R + 0.01, S / 2 - t - y - relief, w]);                   // core inside, clear of the bend
  }
}

module snap_slot(P = P, S = S, w = w, c = slide_fit) difference() {   // the plate the clip clicks through
  translate([-(S + 14) / 2, -(w + 14) / 2, 0]) cube([S + 14, w + 14, P]);
  translate([-(S + c) / 2, -(w + c) / 2, -1]) cube([S + c, w + c, P + 2]);
}

module living_hinge(len = 6, width = 15, ht = 0.5, leaf = [18, 2]) {
  assert(material != "PLA", "a living hinge in PLA cracks after a few hundred folds: use PETG, PP or nylon");
  assert(ht >= 0.3 && ht <= 0.8, "living hinge thickness: 0.3 to 0.8 mm");
  assert(len >= 5, "living hinge: at least 5 mm long, so the bend spreads out");
  cube([leaf[0], width, leaf[1]]);
  translate([leaf[0], 0, 0]) cube([len, width, ht]);            // on the bed: its lines run across the fold
  translate([leaf[0] + len, 0, 0]) cube([leaf[0], width, leaf[1]]);
}

module card() {
  snap_clip();
  translate([8, 30, 0]) snap_slot();
  if (material != "PLA") translate([-base, -40, 0]) living_hinge();
  else echo("no living hinge sample: PLA cracks at a living hinge");
}

if (part == "card" || part == "all") color("#1f1f22") card();
