# Workshop API

Everything the Workshop page does, it does through this API. Use it from scripts, or through the `printer` MCP server
(`tools/printer_mcp.py`), which wraps it for the Claude app.

**Where:** `http://<workshop-host>:8090`, or a private name you give it with `--allow-host` (for example
`workshop.alm.almehdar.co.uk` behind Caddy, reachable at home or on the tailnet only). There is no password: keep Workshop
off the public internet.

**Changes need two headers.** Every request that is not a GET must carry `X-Workshop: 1`, and should carry
`X-Workshop-User: <first name>`, so the activity log says who did it. A request from another website is refused (no CORS
is ever granted, and the Host must be an IP address, `localhost` or an `--allow-host` name).

**Safety is in the server, not the page.** While a print runs, Workshop refuses anything that moves, heats or feeds; only
the light and the speed override (50 to 150%) pass. A start needs the file's heads loaded. A queue that is running cannot
be added to. Cancelling one part refuses the last part left. The bed-clear word before a start is the caller's job: the
MCP's start tool asks for it and checks the camera.

## Reading

| GET | What you get |
| --- | --- |
| `/api/info` | printer, slicer available, presets, live camera and printer screen addresses, phone push link, prices |
| `/moonraker/<path>` | any Moonraker read (status, objects, files, history, queue) through Workshop |
| `/cam.jpg` | a camera still |
| `/screen.jpg` | the printer's touchscreen as a picture (Extended Firmware) |
| `/api/health` | the last bed mesh (rows, spread, verdict) and 20 minutes of every heater's temperature |
| `/api/spoolman` | Spoolman's spools by head: grams left and used, low under 200 g (needs `"spoolman"` in printer.json) |
| `/api/spools` | Workshop's own spool ledger (grams per head, from the printer's measured use) |
| `/api/costs` | filament and cost per print, per month and per person who started it |
| `/api/plan` | the queue as a plan: each job's start and end, and starts that fall in the quiet hours (03:00 to 08:00) |
| `/api/accuracy` | slicer against printer: time, warm-up minutes and filament ratios from finished prints |
| `/api/parts` | the running print's parts (for cancelling one), which is printing, which are cancelled |
| `/parts-map.png?mark=NAME` | the bed from above with each part's outline, NAME filled red, layer 1 drawn in |
| `/api/frames[?job=NAME]` | the layer photos of a print, and which prints have them |
| `/api/photo-checks` | the photo watch's recent verdicts |
| `/api/activity` | who did what, newest first |
| `/api/alerts` | the printer's recent warnings, with advice |
| `/api/designs` | every design on the benches, with real slicer time and grams |
| `/api/requests` | print requests between the two of you |
| `/api/calibration` | recorded calibration results |
| `/api/bed` | whether a finished part is still waiting on the bed |

## Doing

| POST | Body | What it does |
| --- | --- | --- |
| `/moonraker/printer/gcode/script?script=CMD` | none | one G-code command, behind the guard (refused mid-print unless light or speed) |
| `/moonraker/printer/print/start?filename=F` | none | start a file; refused if its heads are not loaded or a job runs |
| `/moonraker/printer/print/pause`, `.../resume`, `.../cancel` | none | pause, resume, cancel the whole print |
| `/api/upload-model?name=part.stl&colour=1c1c1c&preset=lean&queue=1` | the file's bytes | slice an STL (on the head holding that colour) or a 3MF, send it to the printer, optionally queue it; never starts |
| `/api/slice` | `{"bench": 0, "id": "design", "preset": "lean", "set": ["infill=30"], "queue": false}` | slice a bench design and send it |
| `/api/parts` | `{"name": "PART_NAME"}` | stop printing that one part, keep the rest (only after a person saw it marked and said yes) |
| `/api/unload` | `{"head": "T2"}` | unload a head's filament back to the spool (idle only) |
| `/api/filament` | `{"head": "T2", "type": "PETG", "vendor": "...", "colour": "1c1c1c"}` | tell the printer what was loaded |
| `/api/spools` | `{"head": "T1", "spool": "SUNLU PETG black", "grams": 1000}` | a new spool on a head (Workshop's ledger) |
| `/api/bed` | none | "I took the part off": the bed is clear again |
| `/api/requests` | `{"what": "...", "qty": 1, "colour": "..."}` or `{"id": 3, "state": "taken"}` | ask for a print, or move a request along |
| `/api/calibration` | `{"test": "temperature", "value": "240", "filament": "...", "note": "..."}` | record a calibration result with a photo |

## The same from the Claude app

Add the `printer` MCP to your own Claude app with your name:

```json
{"mcpServers": {"printer": {"command": "python3", "args": ["<clone>/tools/printer_mcp.py"],
  "env": {"WORKSHOP_URL": "http://<workshop-host>:8090", "WORKSHOP_USER": "<first name>"}}}}
```

Its tools cover all of the above. They read status, health, spools, costs, plan, history, activity, warnings, the camera, the screen and the layer photos. They slice designs and uploaded models, then send, queue, start (after the bed-clear word and a fresh frame), pause, resume and cancel. They also cancel one part (after the marked picture and a yes), set speed, light, heaters, motors and home (after a nothing-in-the-way yes), unload, set filament, manage print requests and record calibration.
