# Snapmaker U1: what Claude needs to know

Learned on a U1 with stock firmware 2.1.2 (November 2026), driving it from `tools/printer.py` and `tools/workshop.py`. Every item here was seen on the printer or read in Snapmaker's published source ([u1-klipper](https://github.com/Snapmaker/u1-klipper), [u1-moonraker](https://github.com/Snapmaker/u1-moonraker)); nothing is from memory.

## Talking to it

- Moonraker on port 7125, Fluidd on port 80. On a home network the stock firmware answers without an API key, so anything on the network can drive it: keep it off the internet and reach it from outside over a VPN.
- `printer.py` finds it from `--printer`, `PRINTER_HOST`, or `{"host": "..."}` in `~/.config/claude-3d-printing/printer.json`.
- Long commands (unload, homing) hold the HTTP request open and the printer sometimes drops it. Judge success from the printer's state, not the reply.

## Camera

- The still is `/server/files/camera/monitor.jpg`, about one frame a second, 1920 x 1080, low at bed level (it shows the head and the near half of the plate well; a thin leftover part can hide).
- **It only refreshes while something watches it.** Snapmaker's app sends `camera.start_monitor` over Moonraker's websocket (`{"req_id": 1, "domain": "lan", "interval": 1}`); without it the still can be an hour old. `printer.py snap`, `watch` and Workshop check the still's age and wake it when it is older than 10 s. Check the age before trusting any picture.
- 30 fps video needs the community Extended firmware (WebRTC at `/webcam/`); Workshop switches to it by itself when it is there.

## Heads and filament

- Four heads, T0 to T3; the screen numbers them 1 to 4.
- What each head holds: `print_task_config` (`filament_vendor`, `filament_type`, `filament_sub_type`, `filament_color_rgba`, `filament_official` for Snapmaker's chipped spools, `filament_soft` for TPU-like).
- **Setting a spool** (what the screen does after you load one): `SET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER=<n> VENDOR=<v> FILAMENT_TYPE=<t> FILAMENT_SUBTYPE=<s> FILAMENT_COLOR_RGBA=<RRGGBB>FF`. Names come from the printer's own table, `config/snapmaker/filament_parameters.json` (type, then `vendor_*`, then `sub_*`); an unknown vendor or sub-type falls back to the type's generic entry. It refuses chipped spools unless `FORCE=1`, and it resets that head's pressure-advance calibration (the screen does the same). `printer.py filament T2 PETG --vendor SUNLU --colour 1c1c1c`.
- `filament_exist` means a spool is on the holder, not that filament is in the head. The feeder's own state is `filament_feed left|right` → `extruderN.channel_state`: `load_finish` (in the head), `preload_finish` (parked at the feeder), `unload_finish`, `unload_fail`.
- **Unloading** is a batch, exactly as the screen's Unload all sends it: `AUTO_FEEDING_BATCH ACTION=START`; for each head `AUTO_FEEDING_BATCH ACTION=DOING STAGE=prepare EXTRUDER=<n> [NEXT_EXTRUDER=<m>] UNLOAD=1` (pick the head, heat it, preheat the next), then `... STAGE=doing EXTRUDER=<n> UNLOAD=1` (cut, retract, wind back); `AUTO_FEEDING_BATCH ACTION=STOP` at the end (heaters off). A `DOING` outside a batch is refused. `machine_state_manager.main_state` is 0 when idle. `printer.py unload T2|all` does this and only touches heads in `load_finish`.
- **Loading** is the same batch with `LOAD=1` and no stage: `AUTO_FEEDING_BATCH ACTION=DOING EXTRUDER=<n> NEXT_EXTRUDER=<m> LOAD=1`, one head after another (the feeder then flushes: `load_flushing`, then `load_finish`). A new spool only reaches `preload_finish` (parked at the feeder) until it is loaded; **a print started then runs out at the first move** ("e1_filament runout", paused at layer 0, and resuming fails). `printer.py start` and Workshop's Print refuse until every head the file uses reports `load_finish`. Loading is left to the person at the screen, who feeds the filament.

## Slicing for it

- Upstream OrcaSlicer 2.5 ships a U1 profile packed in its own `.opc` format; Snapmaker Orca ships the profiles as plain JSON (`Snapmaker Orca.app/Contents/Resources/profiles/Snapmaker`). `orca.py new-printer` builds a setup from those.
- Snapmaker Orca's profiles use variables upstream OrcaSlicer refuses (`chamber_cooling_mode` in the start G-code). `orca.py adopt-gcode <a good U1 print>.gcode --printer u1` copies the start, end and tool-change G-code from a file that printed well.
- **Filament profiles named `... @U1 base` are incomplete** (no temperature, no density: PETG would slice at 200 °C and 0 g). Use the nozzle-specific ones, for example `Generic PETG @U1 0.4 nozzle` (255 °C nozzle, 80 °C bed, 1.25 g/cm³). Snapmaker Orca 2.4.1 lacks some 0.4 nozzle profiles that 2.4.2 has.
- Snapmaker Orca 2.4.1's command line crashes on macOS (exit 139); 2.4.2 slices. Upstream OrcaSlicer's command line works.
- **One-part objects:** OrcaSlicer uses the object's head setting and ignores the part's, so `make_3mf.py` sets the object to its first part's head (before that, every single-colour print went to T0).
- Map design colours to the head that holds them (`make_3mf.py --heads`, or Workshop, which reads the printer). A colour that is not loaded is refused rather than printed wrong.

## Safety Claude keeps to

- Every start: the person says the bed is clear and they are around (or has told Claude in words it may start on a clear camera check), and a fresh camera frame agrees.
- Nothing that moves, heats or feeds is ever sent as a test. A refusal guard that depends on live state can flip between writing the test and running it.
- Nothing is sent while the person is handling the printer.
