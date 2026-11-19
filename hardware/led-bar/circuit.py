"""Rack status LED bar: the circuit, as code (SKiDL). SPECS.md says why each part is here and where its figures come from.

    python circuit.py        # electrical rules check, then writes led-bar.net (KiCad netlist) and parts.json

parts.json is what tools/pcb.py builds the board from: every part's reference, value, footprint, LCSC number and
which net each pin is on.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "tools"))
import kicad_env  # noqa: E402  points SKiDL at the installed KiCad's libraries

import builtins  # noqa: E402
from skidl import ERC, POWER, Net, Part, generate_netlist, set_default_tool, KICAD10  # noqa: E402

set_default_tool(KICAD10)
NC = builtins.NC   # SKiDL's no-connect net: marks a pin as open on purpose

R0603, C0603, C0805 = "Resistor_SMD:R_0603_1608Metric", "Capacitor_SMD:C_0603_1608Metric", "Capacitor_SMD:C_0805_2012Metric"
N_LEDS = 16


def part(lib, name, fp, value=None, lcsc="", ref=None):
    p = Part(lib, name, footprint=fp)
    if value:
        p.value = value
    p.fields["LCSC"] = lcsc
    if ref:
        p.ref = ref
    return p


# LCSC numbers for the passives: all JLCPCB Basic parts, checked on JLCPCB's listings 19 Nov 2026 (see SPECS.md)
LCSC = {"100nF": "C14663", "1uF": "C15849", "10uF": "C15850", "10k": "C25804", "5.1k": "C23186", "390": "C23151"}


def R(value, ref):
    return part("Device", "R", R0603, value, LCSC[value], ref)


def C(value, ref, fp=C0603):
    return part("Device", "C", fp, value, LCSC[value], ref)


vbus, v5, v33, gnd = Net("VBUS"), Net("+5V"), Net("+3V3"), Net("GND")
dp, dm, en, io9, data3, data5 = Net("USB_DP"), Net("USB_DM"), Net("EN"), Net("BOOT"), Net("LED_DATA_3V3"), Net("LED_DATA_5V")
for n in (vbus, v5, v33, gnd):
    n.drive = POWER   # power arrives through passive parts (connector, fuse); this tells the ERC the rails are driven

# ---- USB-C power and data in ----
j1 = part("Connector", "USB_C_Receptacle_USB2.0_16P", "Connector_USB:USB_C_Receptacle_HCTL_HC-TYPE-C-16P-01A", "USB-C", "C2894897", ref="J1")
vbus += j1["VBUS"]
gnd += j1["GND"], j1["SHIELD"]
dp += j1["D+"]
dm += j1["D-"]
for i, cc in enumerate(("CC1", "CC2")):   # 5.1 kOhm to ground on each CC pin: tells the charger "sink, 5 V please"
    r = R("5.1k", f"R{i + 1}")
    r[1] += j1[cc]
    r[2] += gnd
for sbu in ("SBU1", "SBU2"):
    j1[sbu] += NC   # unused sideband pins, marked no-connect

# ESD protection on the USB data lines and VBUS (each I/O pair is a pass-through)
u4 = part("Power_Protection", "USBLC6-2SC6", "Package_TO_SOT_SMD:SOT-23-6", "USBLC6-2SC6", "C7519", ref="U4")
dp += u4[1], u4[6]
dm += u4[3], u4[4]
gnd += u4[2]
vbus += u4[5]

# Polyfuse: limits a short or a stalled board to 1.5 A, and resets itself
f1 = part("Device", "Polyfuse", "Fuse:Fuse_1206_3216Metric", "1.5A hold", "C20802", ref="F1")
f1[1] += vbus
f1[2] += v5
c_bulk = part("Device", "C_Polarized", "Capacitor_SMD:CP_Elec_8x10", "470uF 16V", "C165531", ref="C1")   # holds the 5 V rail up when 16 LEDs switch at once
c_bulk[1] += v5
c_bulk[2] += gnd

# ---- 3.3 V for the ESP32-C3 ----
u2 = part("Regulator_Linear", "AP2112K-3.3", "Package_TO_SOT_SMD:SOT-23-5", "AP2112K-3.3", "C51118", ref="U2")
v5 += u2["VIN"], u2["EN"]          # always on
gnd += u2["GND"]
v33 += u2["VOUT"]
for net, ref in ((v5, "C2"), (v33, "C3")):   # 1 uF X5R/X7R at the regulator's input and output (AP2112 datasheet, note 4)
    c = C("1uF", ref)
    c[1] += net
    c[2] += gnd

# ---- ESP32-C3 module ----
u1 = part("RF_Module", "ESP32-C3-WROOM-02", "RF_Module:ESP32-C3-WROOM-02", "ESP32-C3-WROOM-02-N4", "C2934560", ref="U1")
v33 += u1["3V3"]
gnd += u1["GND"]
en += u1["EN"]
io9 += u1["IO9"]
dp += u1["IO19"]       # GPIO19 is USB D+ (datasheet table 3-1)
dm += u1["IO18"]       # GPIO18 is USB D-
data3 += u1["IO4"]     # LED data; set "GPIO 4" in WLED's LED preferences
for net, val, ref in ((v33, "10uF", "C4"), (v33, "100nF", "C5")):   # bulk and high-frequency decoupling at the module
    c = C(val, ref, C0805 if val == "10uF" else C0603)
    c[1] += net
    c[2] += gnd
r_en = R("10k", "R3")        # EN pull-up with 1 uF: a clean reset after power is stable
r_en[1] += v33
r_en[2] += en
c_en = C("1uF", "C6")
c_en[1] += en
c_en[2] += gnd
for io, ref in (("IO2", "R4"), ("IO8", "R5")):   # strapping pins: both high so the USB download boot works (datasheet section 4)
    r = R("10k", ref)
    r[1] += v33
    r[2] += u1[io]
for io in ("IO0", "IO1", "IO3", "IO5", "IO6", "IO7", "IO10"):
    u1[io] += NC    # spare GPIO, marked no-connect
for num, name in ((11, "RXD"), (12, "TXD")):   # UART0, unused (the ESP32-C3 talks over native USB)
    u1[num] += NC

sw_reset = part("Switch", "SW_Push", "Button_Switch_SMD:SW_Push_1P1T_XKB_TS-1187A", "RESET", "C318884", ref="SW1")
sw_reset[1] += en
sw_reset[2] += gnd
sw_boot = part("Switch", "SW_Push", "Button_Switch_SMD:SW_Push_1P1T_XKB_TS-1187A", "BOOT", "C318884", ref="SW2")   # held at power-up: download mode; after boot: a WLED button
sw_boot[1] += io9
sw_boot[2] += gnd

# ---- 3.3 V data to 5 V data ----
u3 = part("74xGxx", "74AHCT1G125", "Package_TO_SOT_SMD:SOT-23-5", "74AHCT1G125", "C7484", ref="U3")
gnd += u3[1], u3["GND"]   # pin 1 is OE, active low (TI datasheet): tied to ground, the buffer always drives
data3 += u3[2]             # pin 2 is A, the 3.3 V input
v5 += u3["VCC"]
c = C("100nF", "C7")
c[1] += v5
c[2] += gnd
r_data = R("390", "R6")               # series resistor at the first LED's input: damps ringing on the data line
r_data[1] += u3[4]              # pin 4 is Y, the 5 V output
r_data[2] += data5

# ---- 16 WS2812B in a chain ----
din = data5
for i in range(N_LEDS):
    d = part("LED", "WS2812B", "LED_SMD:LED_WS2812B_PLCC4_5.0x5.0mm_P3.2mm", "WS2812B", "C2761795", ref=f"D{i + 1}")
    v5 += d["VDD"]
    gnd += d["VSS"]
    din += d["DIN"]
    if i < N_LEDS - 1:
        din = Net(f"LED_CHAIN_{i + 1}")
        din += d["DOUT"]
    else:
        d["DOUT"] += NC        # the last LED's output goes nowhere
    c = C("100nF", f"C{8 + i}")  # one 100 nF per LED, at its VDD pin
    c[1] += v5
    c[2] += gnd

# ---- mounting holes (no electrical connection) ----
for i in range(4):
    part("Mechanical", "MountingHole", "MountingHole:MountingHole_3.2mm_M3", "M3", ref=f"H{i + 1}")

if __name__ == "__main__":
    ERC()
    generate_netlist(file_=os.path.join(HERE, "led-bar.net"))
    default_circuit = builtins.default_circuit
    parts = []
    for p in default_circuit.parts:
        pins = {str(pin.num): (pin.net.name if pin.net else None) for pin in p.pins}
        parts.append({"ref": p.ref, "value": str(p.value), "footprint": p.footprint, "lcsc": p.fields.get("LCSC", ""), "pins": pins})
    json.dump(parts, open(os.path.join(HERE, "parts.json"), "w"), indent=1)
    print(f"{len(parts)} parts, written led-bar.net and parts.json")
