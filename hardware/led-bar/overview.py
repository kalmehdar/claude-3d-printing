"""Overview drawing of the rack status LED bar: the power path along the top, control and data below.

    python overview.py            # writes overview.svg next to this file

This is the readable picture for the README and the Print Bench. The full schematic is the KiCad one made from
circuit.py; if the two ever disagree, circuit.py is right.
"""
import os

import schemdraw
import schemdraw.elements as elm
import schemdraw.flow as flow

schemdraw.config(fontsize=11, font="sans-serif", lw=1.4)
RED, BLUE = "#c8102e", "#002fa7"
PALE, POWER, LOGIC = "#ecebe6", "#f6d8dc", "#dfe5f5"


def box(d, xy, text, fill, w=2.6, h=1.3):
    return d.add(flow.Box(w=w, h=h).at(xy).anchor("center").label(text).fill(fill))


def arrow(d, a, b, colour, text="", loc="top"):
    d.add(elm.Line(arrow="->").at(a).to(b).color(colour).label(text, loc=loc, color=colour))


with schemdraw.Drawing(show=False) as d:
    usb = box(d, (0, 0), "USB-C\n5 V in", PALE)
    esd = box(d, (4, 0), "ESD\nUSBLC6", PALE)
    fuse = box(d, (8, 0), "Polyfuse\n1.5 A hold", PALE)
    rail = box(d, (12.4, 0), "5 V rail\n470 uF bulk", POWER, w=2.8)
    leds = box(d, (17.2, 0), "16 x WS2812B\n48 mA max each", POWER, w=3.4)
    ldo = box(d, (12.4, -3.2), "LDO 3.3 V\nAP2112K", PALE, w=2.8)
    mcu = box(d, (8, -3.2), "ESP32-C3-WROOM-02\nWi-Fi\nBOOT + RESET buttons", LOGIC, w=3.6, h=1.6)
    shift = box(d, (8, -6.4), "Level shift\n74AHCT1G125", LOGIC, w=3.0)
    res = box(d, (12.4, -6.4), "390 Ohm\nseries", LOGIC, w=2.4)

    arrow(d, usb.E, esd.W, RED)
    arrow(d, esd.E, fuse.W, RED)
    arrow(d, fuse.E, rail.W, RED, "+5V")
    arrow(d, rail.E, leds.W, RED)
    arrow(d, rail.S, ldo.N, RED)
    arrow(d, ldo.W, mcu.E, RED, "+3V3")
    arrow(d, mcu.S, shift.N, BLUE, "  GPIO, 3.3 V", loc="bottom")
    arrow(d, shift.E, res.W, BLUE, "5 V logic")
    d.add(elm.Wire("-|", arrow="->").at(res.E).to(leds.S).color(BLUE).label("DIN", loc="right", color=BLUE))
    d.add(elm.Wire("|-", arrow="->").at(usb.S).to((mcu.W[0] - 0.01, mcu.W[1] - 0.5)).color(BLUE))
    d.add(elm.Label().at((0.2, -4.6)).label("USB D+ / D-\n(flashing, serial)", color=BLUE, halign="left"))

    d.save(os.path.join(os.path.dirname(os.path.abspath(__file__)), "overview.svg"))
print("wrote overview.svg")
