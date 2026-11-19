"""Where every part sits on the LED bar board, written to board.json for tools/pcb.py.

    python layout.py

Coordinates are millimetres from the board's top-left corner, x right, y down, rotation in degrees.
The reasons are in SPECS.md: LEDs in a centred row along the top, the ESP32 module in the middle with its antenna at
the bottom edge (nothing under the antenna), power parts to the left, the data path to the right.
"""
import json
import os

W, H = 200.0, 36.0
place = {}

# LEDs: 16 in a row, 12 mm apart, centred. D1 is the right-most so the data path from the level shifter is short.
for i in range(16):
    x = 186 - 11.5 * i   # 11.5 mm apart: the end LEDs clear the corner holes
    place[f"D{i + 1}"] = (x, 7.0, 0)
    place[f"C{8 + i}"] = (x - 6, 7.0, 90) if i < 15 else (x, 13.0, 0)   # 100 nF between LEDs, next to each VDD pin

# ESP32-C3-WROOM-02: rotated so the antenna end points down, antenna keep-out over the bottom edge
place["U1"] = (100, 17.6, 180)

# Power: USB-C on the left edge, then ESD, fuse, bulk capacitor, regulator
place["J1"] = (4.2, 22, 270)
place["R1"] = (13, 16.5, 0)
place["R2"] = (13, 27.5, 0)
place["U4"] = (16, 22, 0)
place["F1"] = (24, 22, 90)
place["C1"] = (36, 22, 0)
place["U2"] = (76, 21, 0)
place["C2"] = (71, 21, 90)
place["C3"] = (81, 21, 90)
place["C4"] = (78, 27, 0)
place["C5"] = (78, 30.5, 0)

# Control and data: reset and boot buttons, strapping pull-ups, the level shifter and its series resistor
place["R3"] = (121, 18, 90)
place["C6"] = (124, 18, 90)
place["R4"] = (121, 24, 90)
place["R5"] = (124, 24, 90)
place["SW1"] = (134, 27, 0)
place["SW2"] = (144, 27, 0)
place["U3"] = (176, 20, 0)
place["C7"] = (176, 15, 0)
place["R6"] = (184, 20, 90)

# M3 holes, 4 mm in from each corner
for i, (x, y) in enumerate(((4, 4), (196, 4), (4, 32), (196, 32))):
    place[f"H{i + 1}"] = (x, y, 0)

out = {"width": W, "height": H, "corner_radius": 2.0, "tallest": 10.0,   # C1, the 470 uF capacitor (Samyoung 8 x 10 mm)
       "rules": {"track": 0.25, "clearance": 0.2, "via": 0.6, "drill": 0.3, "edge_clearance": 0.5,
                 "min_track": 0.15, "min_drill": 0.2, "hole_clearance": 0.18},   # JLCPCB standard allows 0.127 mm track; 0.2 mm drill and 0.18 mm hole clearance are the module and USB-C library footprints' own
       "power_nets": {"+5V": 0.8, "VBUS": 0.5, "GND": 0.5, "+3V3": 0.5},   # VBUS 0.5: reaches the USB-C pads, 1.3 A needs 0.43; GND is mostly the pour
       "ground_pour": "GND",
       "place": {k: list(v) for k, v in place.items()}}
json.dump(out, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "board.json"), "w"), indent=1)
print(f"{len(place)} parts placed, board.json written")
