---
name: electronics-design
description: 'Designs small low-voltage electronics with the person: requirements, a specs sheet, part choices with verified datasheets, a power budget, protection, and a schematic written as code. Use when someone says "design a circuit", "I want a board that ...", "what parts do I need", "power budget", "spec this out", "wire up these LEDs/sensors", "schematic for ...", or brings an ESP32, Arduino, Raspberry Pi, LED strip or sensor project. Hands over to pcb-layout for the board and enclosure-from-pcb for the case.'
---

# Electronics design

Tools: `tools/` two folders above this skill's base directory (the plugin root, or the repo root in a clone).

Scope: low-voltage DC projects (USB, 5 V, 12 V, batteries with a proper protection board). **Never mains**: anything wired to the wall goes through a certified, enclosed power supply, and Claude does not design the mains side. Say so plainly if asked.

The order: **requirements, spec sheet, power budget, parts, schematic as code, review.** Nothing goes to a PCB until the spec sheet and the power budget agree.

## 1. Requirements (one message)

Ask only what is missing: what it does, the power source (USB-C, a 5 V or 12 V supply, a battery), how it is controlled (button, Wi-Fi, a USB host), what it connects to (strip, sensor, display) and the size it has to fit. Do not invent requirements; list assumptions so the person can correct them.

## 2. The spec sheet: `hardware/<name>/SPECS.md`

One table per section, every number with its source:

- **Purpose** in two lines.
- **Interfaces:** each connector with its pinout (pin, signal, voltage, max current).
- **Electrical:** input voltage range, worst-case current, logic levels.
- **Mechanical:** board size, mounting holes (M2.5 or M3 at the corners, 3 mm from the edges unless the case says otherwise), connector positions, the tallest part.
- **Environment:** indoor, temperature range if it matters.
- **Open questions:** anything the person must decide or measure.

## 3. Power budget (always, as a table)

Every load at its worst case, the total, and the supply's rating with headroom (supply at least 1.25 x the worst case). Write the sums with a one-line `python -c` rather than in your head. Examples of worst cases: a WS2812B LED at full white is about 60 mA at 5 V; a USB 2.0 port gives 500 mA unless the device negotiates more over USB-C (5.1 kOhm pull-downs on CC1 and CC2 get up to 3 A at 5 V only from a charger that offers it, so say which).

## 4. Parts

- Prefer parts an assembly service stocks (JLCPCB basic parts, LCSC numbers) and through-hole or 0805 and up when the person will hand-solder.
- **Check every part against its datasheet before using it**: open the datasheet (manufacturer's site or LCSC) and record the URL, the pin numbering and the absolute maximum ratings in SPECS.md. A part number Claude "remembers" is a claim to verify, never a fact.
- Standard protection on any board with external power: reverse-polarity protection (a P-MOSFET or a Schottky diode), a polyfuse or fuse sized from the budget, a TVS diode on the input if the leads are long, 100 nF decoupling at every IC power pin plus bulk capacitance at the input.
- Recurring patterns:
  - **Addressable LEDs (WS2812B, SK6812):** 5 V supply, a 300 to 500 Ohm resistor in series with the data line near the first LED, at least 470 uF across the strip's power at its input, and a 3.3 V to 5 V level shifter (74AHCT125 or 74HCT245) when a 3.3 V microcontroller drives it. Inject power every 1 to 2 m on long runs.
  - **USB-C power only:** 5.1 kOhm from CC1 and from CC2 to ground, VBUS through the fuse.
  - **Buttons:** to ground with the microcontroller's pull-up, optional 100 nF for debounce.
  - **I2C sensors:** 4.7 kOhm pull-ups to 3.3 V on SDA and SCL, once per bus.

## 5. Schematic as code: `hardware/<name>/circuit.py`

Write the circuit in SKiDL (Python): every part from the KiCad libraries with its footprint, every net named (`VBUS`, `+5V`, `GND`, `LED_DATA`), and a comment on each protection and passive part saying why it is there. Run it to produce the netlist and run its ERC; zero errors before moving on. Also write `hardware/<name>/overview.py`, a schemdraw drawing of the circuit saved as `overview.svg`, for the README and the bench, so people can follow it without KiCad. Keep it to blocks and the signal path; KiCad's schematic is the full one.

## 6. Review before any board

Read the netlist back against SPECS.md: every connector pin on the right net, every IC powered and decoupled, no floating inputs, ratings inside the absolute maximums with margin. Ask an independent reviewer (a fresh agent given the files, not your conclusion) for anything that will carry real current. Then hand over to `pcb-layout`.

## Never

Mains wiring. Lithium cells without a protection board and a charger IC made for them. A part used without its datasheet checked. Calling a design "safe": say which checks passed.
