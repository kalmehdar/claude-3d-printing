# Rack status LED bar: specs

A 1U panel for a 10-inch rack: 16 addressable LEDs behind a printed faceplate with a white diffuser strip, run by an ESP32-C3 with [WLED](https://kno.wled.ge/) firmware, powered and flashed over one USB-C cable. Designed with Claude using the `electronics-design` skill; every part figure below comes from the datasheet linked beside it.

Status: **design, not yet built or ordered.** Read [REVIEW.md](REVIEW.md) before ordering.

## Interfaces

| Connector | Pin | Signal | Level | Max current |
| --- | --- | --- | --- | --- |
| J1 USB-C (power and data) | VBUS | +5 V in | 5 V | 1.5 A through F1 |
| | D+ / D- | ESP32-C3 native USB (flashing, serial) | 3.3 V | |
| | CC1 / CC2 | 5.1 kOhm to GND each (asks for 5 V; up to 3 A from a source that offers it) | | |
| | GND | | | |

No other connectors: the LEDs are on the board.

## Electrical

| Item | Value | Source |
| --- | --- | --- |
| Input | 5 V USB-C, 4.5 to 5.5 V | USB spec; WS2812B runs at 4.5 to 5.5 V ([datasheet](https://www.triopak.fi/files/product/Worldsemi/WS2812B.pdf)) |
| Logic | 3.3 V from an AP2112K-3.3 LDO, 600 mA, 250 mV dropout at 600 mA | [AP2112 datasheet](https://www.diodes.com/assets/Datasheets/AP2112.pdf) |
| ESP32-C3 supply | 3.0 to 3.6 V, supply able to give 0.5 A | [ESP32-C3-WROOM-02 datasheet v1.7](https://documentation.espressif.com/esp32-c3-wroom-02_datasheet_en.pdf), table 6-2 |
| LED data | GPIO4, through a 74AHCT1G125 to 5 V logic (WS2812B needs 0.7 x VDD = 3.5 V for a high; AHCT reads 2.0 V as high) | [SN74AHCT1G125](https://www.ti.com/lit/ds/symlink/sn74ahct1g125.pdf), WS2812B datasheet |
| Button | GPIO9 (BOOT, held at power-up to force download mode; a WLED button after boot); EN reset button | ESP32-C3 datasheet section 4 |

### Power budget

| Load | Worst case | Note |
| --- | --- | --- |
| 16 x WS2812B at full white | 768 mA | 16 mA per colour x 3 x 16 |
| 3.3 V branch (ESP32-C3, level shifter) | 500 mA | Espressif's supply requirement, not typical draw |
| **Total** | **1268 mA** | |
| Supply needed (x 1.25) | 1585 mA | a 5 V / 2 A or better USB-C charger |

On a plain USB port (500 mA) WLED's automatic brightness limiter must be set to **400 mA**; with a 2 A or better charger, **1400 mA**. WLED applies this in software (Settings, LED Preferences).

Power traces: 1.3 A needs 0.43 mm at a 10 C rise (`tools/trace_width.py 1.3`); the board uses 0.8 mm for +5V and GND feeds. F1 is a 1.5 A hold polyfuse.

## Mechanical

| Item | Value |
| --- | --- |
| Board | 200 x 36 mm, 1.6 mm FR-4, 2 layers, all parts on the top (front) side |
| LEDs | 16 x WS2812B 5050 in a row, 11.5 mm apart (13.5 to 186 mm from the left edge), centred, 7 mm below the top edge |
| ESP32-C3 module | ESP32-C3-WROOM-02, 18 x 20 mm, in the middle under the LED row, antenna toward the bottom edge over the keep-out (no copper on either layer, datasheet figure 3-1) |
| USB-C | left edge, opening sideways; power parts beside it, the data path at the right end (the chain starts at D1, the right-most LED) |
| Mounting holes | 4 x M3 (3.2 mm) at (4, 4), (196, 4), (4, 32), (196, 32) mm, for standoffs on the printed faceplate |
| Faceplate | printed 1U 10-inch blank (254 x 44.45 mm) with a 180 x 8 mm diffuser window in white PETG over the LED row |
| Tallest part | C1, the 470 uF capacitor: 10 mm. The module is 3.2 mm |

## Parts (JLCPCB assembly)

| Ref | Part | LCSC | Datasheet |
| --- | --- | --- | --- |
| U1 | ESP32-C3-WROOM-02-N4 (4 MB) | C2934560 | [Espressif v1.7](https://documentation.espressif.com/esp32-c3-wroom-02_datasheet_en.pdf) |
| U2 | AP2112K-3.3TRG1 | C51118 | [Diodes](https://www.diodes.com/assets/Datasheets/AP2112.pdf) |
| U3 | SN74AHCT1G125DBVR | C7484 | [TI](https://www.ti.com/lit/ds/symlink/sn74ahct1g125.pdf) |
| U4 | USBLC6-2SC6 (USB ESD) | C7519 | [ST](https://datasheet.octopart.com/USBLC6-2SC6-STMicroelectronics-datasheet-525477.pdf) |
| D1-D16 | WS2812B-B/T | C2761795 | [Worldsemi](https://www.triopak.fi/files/product/Worldsemi/WS2812B.pdf) |

| J1 | HCTL HC-TYPE-C-16P-01A USB-C | C2894897 | |
| F1 | Ruilon SMD1206P150TF, 1.5 A hold, 6 V | C20802 | |
| C1 | Samyoung 470 uF 16 V, 8 x 10 mm (10 mm tall: the tallest part) | C165531 | |
| SW1, SW2 | XKB TS-1187A tactile switch | C318884 | |

Passives, all 0603 unless noted and all JLCPCB Basic parts: 100 nF C14663 (one per LED and per IC), 1 uF C15849 (LDO in and out, EN), 10 uF 0805 C15850 (3V3), 10 kOhm C25804 (EN and the strapping pull-ups on GPIO2 and GPIO8), 5.1 kOhm C23186 (CC1, CC2), 390 Ohm C23151 (data line). The shifter's OE is tied to GND so it always drives. Every LCSC number was checked on JLCPCB's listing on 19 Nov 2026; stock changes, so check again before ordering.

The board files come from one command: `python tools/pcb.py all hardware/led-bar` (ERC, layout, autoroute, DRC, fab files). Last run: ERC 0 errors, DRC 0 errors, 0 unconnected, 0 warnings.

## Open questions

1. Changed from the ESP32-C3-MINI-1 to the WROOM-02 (19 Nov): LCSC marks the MINI-1-N4 not recommended for new designs, and KiCad 10 ships no MINI-1 symbol or footprint. The WROOM-02 has the same chip and pins in KiCad's own library, checked against Espressif's datasheet.
2. The switches changed from the TL3342 (7 in stock) to the XKB TS-1187A, with KiCad's own footprint for it.
3. Faceplate diffuser thickness: 0.8 mm white PETG is the starting point; print a test strip first.
