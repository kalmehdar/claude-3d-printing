"""Workshop: one page to watch, queue, preview and print on a Klipper/Moonraker printer, with the designs from this repo.

    python tools/workshop.py --printer 192.168.1.50                 serves http://<this machine>:8090
    python tools/workshop.py --printer 192.168.1.50 --bench examples/bench.json --profile u1 --port 8090

What it serves:
    /                     the Workshop page (tools/workshop.html)
    /moonraker/<path>     the printer's Moonraker API, passed through (so the page works from a phone, and later over a VPN)
    /cam.jpg              the printer's camera still (Snapmaker U1 stock firmware: /server/files/camera/monitor.jpg)
    /repo/<path>          files from this repo (the designs' STLs and sources), read only
    /api/designs          the bench.json designs with file links and real slicer numbers for --profile
    /api/slice  (POST)    {"id": design, "preset": "standard", "set": ["infill=30"], "upload": true, "queue": false}
                          packs the design's colour parts into a 3MF, slices it with tools/orca.py and uploads the G-code

Moonraker on many printers has no password. Run this only on a home network or behind a VPN such as Tailscale,
never on an open port. Standard library only; the page loads three.js from a CDN.
"""
import argparse
import http.server
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request

TOOLS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(TOOLS)
sys.path.insert(0, TOOLS)

CFG = {}
SKIP = {"host", "connection", "transfer-encoding", "content-length", "keep-alive", "content-encoding"}


def designs():
    bench_path = CFG["bench"]
    bench = json.load(open(bench_path, encoding="utf-8"))
    base = os.path.dirname(bench_path)
    stats_path = os.path.join(base, "slice-stats.json")
    stats = json.load(open(stats_path, encoding="utf-8")).get(CFG["profile"], {}) if os.path.exists(stats_path) else {}

    def link(p):
        full = os.path.normpath(os.path.join(base, p))
        return "/repo/" + os.path.relpath(full, REPO).replace(os.sep, "/") if full.startswith(REPO) else None

    out = []
    for m in bench["models"]:
        parts = [{"name": e[0], "stl": link(e[1]), "colour": e[2],
                  "printed": not (len(e) > 3 and e[3] == "not printed")} for e in m["parts"]]
        out.append({"id": m["id"], "name": m["name"], "for": m.get("for", ""), "material": m.get("material", ""),
                    "status": m.get("status", ""), "state": m.get("state", ""),
                    "source": link(m["source"]) if m.get("source") else None, "parts": parts,
                    "slice": stats.get("designs", {}).get(m["id"])})
    return {"title": bench.get("title", "Designs"), "bed": bench.get("bed", 256),
            "printer_label": stats.get("label", CFG["profile"]), "designs": out}


def safe_name(s):
    return re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-") or "print"


def upload_gcode(path, name):
    boundary = "workshop" + str(int(time.time() * 1000))
    data = open(path, "rb").read()
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(CFG["moonraker"] + "/server/files/upload", data=body, method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)


def slice_design(req):
    """Returns (http status, dict). Never raises for a slicer problem: the page shows the message."""
    import make_3mf
    import orca
    d = next((m for m in json.load(open(CFG["bench"], encoding="utf-8"))["models"] if m["id"] == req.get("id")), None)
    if not d:
        return 404, {"error": f"no design {req.get('id')!r}"}
    base = os.path.dirname(CFG["bench"])
    parts = [(e[0], os.path.join(base, e[1]), e[2]) for e in d["parts"] if not (len(e) > 3 and e[3] == "not printed")]
    if not parts:
        return 400, {"error": "this design has nothing to print"}
    preset = req.get("preset") or "standard"
    sets = [s for s in req.get("set", []) if re.fullmatch(r"[A-Za-z0-9_-]+=[^;\s]{1,40}", s)]
    out = tempfile.mkdtemp(prefix="workshop-")
    try:
        data, _ = make_3mf.build(parts, d["name"], CFG["bed"])
        p3 = os.path.join(out, safe_name(d["id"]) + ".3mf")
        open(p3, "wb").write(data)
        s = orca.slice_([p3], CFG["profile"], preset=preset, sets=sets, out=out)
    except SystemExit as e:   # orca.py and make_3mf stop with a plain message
        return 422, {"error": str(e.code), "hint": "Slicing needs OrcaSlicer (ORCA_CLI) and a printer setup "
                     f"named {CFG['profile']!r} (see tools/orca.py)."}
    if s.get("error") or not s.get("gcode"):
        return 422, {"error": s.get("error", "no G-code")}
    name = f"{safe_name(d['id'])}-{preset}.gcode"
    result = {"time": s["time"], "grams": s["grams"], "total_g": s["total_g"], "changes": s["changes"],
              "preset": preset, "settings": s["settings"], "file": name}
    if req.get("upload", True):
        upload_gcode(s["gcode"], name)
        result["uploaded"] = True
        if req.get("queue"):
            moonraker("POST", "/server/job_queue/job?filenames=" + urllib.request.quote(name))
            result["queued"] = True
    return 200, result


def moonraker(method, path, body=None, headers=None, timeout=30):
    req = urllib.request.Request(CFG["moonraker"] + path, data=body, method=method, headers=headers or {})
    return urllib.request.urlopen(req, timeout=timeout)


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if CFG.get("verbose"):
            sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def send(self, code, body, ctype="application/json", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else None

    def proxy(self, path, timeout=30):
        hdrs = {k: v for k, v in self.headers.items() if k.lower() not in SKIP}
        try:
            r = moonraker(self.command, path, self.body(), hdrs, timeout)
        except urllib.error.HTTPError as e:
            r = e
        except OSError as e:
            return self.send(502, {"error": f"printer unreachable: {e}"})
        data = r.read()
        self.send(r.status, data, r.headers.get("Content-Type", "application/octet-stream"))

    def route(self):
        p = self.path
        if p in ("/", "/index.html"):
            return self.send(200, open(os.path.join(TOOLS, "workshop.html"), "rb").read(), "text/html; charset=utf-8")
        if p.startswith("/moonraker/"):
            return self.proxy(p[len("/moonraker"):], timeout=300 if "upload" in p else 30)
        if p.startswith("/cam.jpg"):
            return self.proxy(CFG["camera"], timeout=10)
        if p == "/api/designs":
            return self.send(200, designs())
        if p == "/api/info":
            import orca
            try:
                orca.orca_cli()
                slicer = True
            except SystemExit:
                slicer = False
            return self.send(200, {"printer": CFG["printer"], "profile": CFG["profile"], "slicer": slicer,
                                   "presets": getattr(orca, "PRESET_WORDS", {})})
        if p == "/api/slice" and self.command == "POST":
            try:
                code, res = slice_design(json.loads(self.body() or b"{}"))
            except (OSError, ValueError) as e:
                code, res = 500, {"error": str(e)}
            return self.send(code, res)
        if p.startswith("/repo/") and self.command == "GET":
            rel = urllib.request.unquote(p[len("/repo/"):].split("?")[0])
            full = os.path.realpath(os.path.join(REPO, rel))
            if not full.startswith(REPO + os.sep) or "/." in full[len(REPO):] or not os.path.isfile(full):
                return self.send(404, {"error": "not found"})
            ctype = {".stl": "model/stl", ".scad": "text/plain; charset=utf-8", ".glb": "model/gltf-binary",
                     ".json": "application/json"}.get(os.path.splitext(full)[1].lower(), "application/octet-stream")
            return self.send(200, open(full, "rb").read(), ctype)
        return self.send(404, {"error": "not found"})

    do_GET = do_POST = do_DELETE = route


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--printer", default=os.environ.get("PRINTER_HOST"), help="printer address (or PRINTER_HOST)")
    ap.add_argument("--moonraker-port", type=int, default=7125)
    ap.add_argument("--camera", default="/server/files/camera/monitor.jpg", help="camera still path on Moonraker")
    ap.add_argument("--bench", default=os.path.join(REPO, "examples", "bench.json"))
    ap.add_argument("--profile", default="u1", help="tools/orca.py printer setup for slicing")
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    if not a.printer:
        sys.exit("give --printer <address> (or set PRINTER_HOST)")
    bench = os.path.abspath(a.bench)
    CFG.update(printer=a.printer, moonraker=f"http://{a.printer}:{a.moonraker_port}", camera=a.camera, bench=bench,
               profile=a.profile, verbose=a.verbose, bed=json.load(open(bench, encoding="utf-8")).get("bed", 256))
    srv = http.server.ThreadingHTTPServer((a.bind, a.port), Handler)
    print(f"Workshop on http://{a.bind}:{a.port} for printer {a.printer} (Ctrl+C stops)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
