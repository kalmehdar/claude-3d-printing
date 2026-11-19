# Before ordering: review checklist

An autorouted board designed with Claude is a first pass. A person ticks every line here, in KiCad, before any board is ordered. Claude never orders or uploads.

## Checks the tools already ran

- [ ] `pcb.py check` shows ERC 0 errors and DRC 0 errors, 0 unconnected (paste the summary line here)
- [ ] An independent review (a fresh Claude given the files, not the designer's notes) found no P0 or P1 problem

## Footprints against the parts

- [ ] Every footprint matches its BOM part: package size, pin 1 mark, polarity (LEDs: the notched corner; USB-C: shield tabs)
- [ ] WS2812B pin order: 1 VDD, 2 DOUT, 3 VSS, 4 DIN (Worldsemi datasheet)
- [ ] AP2112K: 1 VIN, 2 GND, 3 EN, 5 VOUT; EN tied to VIN
- [ ] 74AHCT1G125: 1 OE to GND, 2 A from GPIO4, 3 GND, 4 Y to the 390 Ohm, 5 VCC to +5V
- [ ] ESP32-C3-WROOM-02: GPIO18 (pin 13) is D-, GPIO19 (pin 14) is D+ (not swapped); pins 9 and 19 (the pad) to GND

## Power

- [ ] +5V and GND feeds at least 0.8 mm wide from F1 to the LED row and the LDO
- [ ] 470 uF bulk capacitor polarity matches its silkscreen
- [ ] 100 nF next to every LED's VDD pin

## Radio and USB

- [ ] No copper on either layer under the ESP32 antenna, and the antenna at the board edge
- [ ] USB D+ and D- run side by side, short, and away from the antenna

## Mechanical

- [ ] Board 200 x 36 mm; holes M3 at 4 mm from each corner, matching the faceplate standoffs
- [ ] USB-C reachable with the panel in the rack

## Sign-off

Checked by: ____________ Date: ________
