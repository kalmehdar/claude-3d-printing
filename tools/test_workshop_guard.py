"""Checks Workshop's guard() refusals that need no printer: bodies on starts and G-code, uploads that would start
themselves, starts while the last part is on the bed, and the Windows key-file rule.  python tools/test_workshop_guard.py"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import workshop as w  # noqa: E402

guard = next(v for v in vars(w).values() if isinstance(v, type) and hasattr(v, "guard")).guard


class Req:
    def __init__(self, command, body=None):
        self.command, self._body = command, body

    def body(self):
        return self._body

    def printer_busy(self):
        return False


tmp = tempfile.mkdtemp()
w.BED = os.path.join(tmp, "bed.json")
upload_print = (b'--x\r\nContent-Disposition: form-data; name="file"; filename="a.gcode"\r\n\r\nG1\r\n'
                b'--x\r\nContent-Disposition: form-data; name="print"\r\n\r\ntrue\r\n--x--\r\n')

# a body on a start or a G-code command is refused, whatever the address says
assert "address" in guard(Req("POST", b'{"script": "G28"}'), "/printer/gcode/script?script=SET_LED%20LED%3Dcavity_led%20WHITE%3D1")
assert "address" in guard(Req("POST", b'{"filename": "other.gcode"}'), "/printer/print/start?filename=good.gcode")
assert "address" in guard(Req("POST", b'{"x": 1}'), "/server/job_queue/start")

# an upload that would start itself is refused; a plain upload passes this check
assert "start it" in guard(Req("POST", upload_print), "/server/files/upload")
# a raw start command skips the bed, snapshot and heads checks: refused even on an idle printer
import urllib.parse  # noqa: E402
for cmd in ("SDCARD_PRINT_FILE FILENAME=a.gcode", "M23 a.gcode", "M24", "m32 a.gcode", "M 24", " m 23 a.gcode"):
    assert "Files or the queue" in guard(Req("POST"), "/printer/gcode/script?script=" + urllib.parse.quote(cmd)), cmd
# Klipper drops anything before the first letter ("0M24" runs M24, "N5 SAVE_CONFIG" saves): refused outright (#54 review)
for cmd in ("0M24", "-m24", "1M23 a.gcode", "N5 SAVE_CONFIG", "5SAVE_CONFIG"):
    assert "prefix" in guard(Req("POST"), "/printer/gcode/script?script=" + urllib.parse.quote(cmd)), cmd
assert w.gcode_name("M 24") == "M24" and w.gcode_name("SET_LED LED=x WHITE=1") == "SET_LED" and w.gcode_name("g1 x5") == "G1"
assert guard(Req("POST"), "/printer/gcode/script?script=" + urllib.parse.quote("M500")) and guard(Req("POST"), "/printer/gcode/script?script=M%20500")
import io  # noqa: E402
real_mr, sent = w.moonraker, []
w.moonraker = lambda m, path, **k: sent.append(path) or io.BytesIO(json.dumps({"result": {"status": {
    "print_stats": {"state": "standby"}, "toolhead": {"homed_axes": "", "position": [0, 0, 0, 0]}}}}).encode())
assert w.control({"action": "gcode", "command": "SDCARD_PRINT_FILE FILENAME=a.gcode"})[0] == 400
assert not any("gcode/script" in x for x in sent), sent
w.moonraker = real_mr
# the heads check reads the one filename Moonraker starts
assert "one file" in guard(Req("POST"), "/printer/print/start?xfilename=ok.gcode&filename=real.gcode")
assert "one file" in guard(Req("POST"), "/printer/print/start?filename=a.gcode&filename=b.gcode")
assert "start it" in guard(Req("POST", b"x"), "/server/files/upload?print=true")
# Moonraker reads root and print from the form too: refused in any quoting (round review, 25 Nov: root=config in the
# body would have written printer.cfg); a file-only upload passes
for field in (b'name="root"', b"name=root", b"name='root'", b"name=print", b"name='print'", b'name = "print"'):
    body = upload_print.replace(b'name="print"', field)
    assert "no print or root" in guard(Req("POST", body), "/server/files/upload"), field
plain = b'--x\r\nContent-Disposition: form-data; name="file"; filename="root.gcode"\r\n\r\nG1 ; name=print\r\n--x--\r\n'
assert guard(Req("POST", plain), "/server/files/upload") is None
# an allowlist: RFC 2231 names, folded headers, any second part, no part at all; print in the address in any encoding
for bad in (plain.replace(b'name="file"', b"name*=utf-8''print"),
            plain.replace(b'form-data; name="file"', b'form-data\r\n ; name="print"'),
            plain.replace(b'--x--', b'--x\r\nContent-Disposition: form-data; name="path"\r\n\r\nsub\r\n--x--'),
            b"just bytes"):
    assert "no print or root" in guard(Req("POST", bad), "/server/files/upload"), bad
for q in ("?print=%74rue", "?%70rint=true", "?print=false", "?print"):
    assert "no print or root" in guard(Req("POST", plain), "/server/files/upload" + q), q

# no start while the last print is still on the bed
json.dump({"state": "part waiting", "file": "a.gcode"}, open(w.BED, "w"))
assert "still on the bed" in guard(Req("POST"), "/printer/print/start?filename=b.gcode")
assert "still on the bed" in guard(Req("POST"), "/server/job_queue/start")
w.bed("clear", who="test")
assert w.bed()["state"] == "clear"

# Windows: a key file counts though its mode bits say others can read it
key = os.path.join(tmp, "anthropic-key")
open(key, "w").write("test-key-not-real")
w.API_KEY_FILE = key
os.environ.pop("ANTHROPIC_API_KEY", None)
if os.name == "nt":
    assert w.api_keys() == ["test-key-not-real"], "the Windows key file was skipped"
open(key, "w").write("ANTHROPIC_API_KEY=test-key-not-real\n")   # put-key writes the NAME=value form
if os.name == "nt":
    assert w.api_keys() == ["test-key-not-real"], "put-key's NAME=value line was not read"
print("workshop guard checks passed")

# a chunked body (Caddy over https, 22 Nov 2026) is read in full and the next request stays intact behind it
import io as _io
class _B: body = w.Handler.body
_b = _B(); _b.headers = {"Transfer-Encoding": "chunked"}; _b.rfile = _io.BytesIO(b'7\r\n{"a": 1\r\n1\r\n}\r\n0\r\n\r\nGET / HTTP/1.1\r\n')
assert _b.body() == b'{"a": 1}' and _b.rfile.read() == b"GET / HTTP/1.1\r\n"
# a negative or junk length must not read to the end of the stream (22 Nov 2026 review, P1)
for _h, _raw in (({"Transfer-Encoding": "chunked"}, b"-1\r\n" + b"x" * 100), ({"Content-Length": "-1"}, b"x" * 100),
                 ({"Content-Length": "abc"}, b"x" * 100), ({"Transfer-Encoding": "chunked"}, b"zz\r\n" + b"x" * 100)):
    _b = _B(); _b.headers = _h; _b.rfile = _io.BytesIO(_raw)
    assert _b.body() is None and _b.close_connection and len(_b.rfile.read()) >= 90, _h
print("chunked body check passed")

# On a real socket (22 Nov 2026 review, P0): a refused POST whose body hides a second request must get one answer and a
# closed connection, never a second answer for the smuggled request; and a kept-alive pair each read their own body.
import socket as _socket, threading as _threading, http.server as _hs
_srv = _hs.ThreadingHTTPServer(("127.0.0.1", 0), w.Handler); _port = _srv.server_address[1]
_threading.Thread(target=_srv.serve_forever, daemon=True).start()
w.CFG.setdefault("allow_hosts", [])
def _talk(raw):
    c = _socket.create_connection(("127.0.0.1", _port), timeout=5); c.sendall(raw); out = b""
    try:
        while True:
            d = c.recv(65536)
            if not d: break
            out += d
    except _socket.timeout:
        pass
    c.close(); return out
_inner = b"POST /api/bed HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Workshop: 1\r\nContent-Length: 0\r\n\r\n"
_out = _talk(b"POST /api/control HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: text/plain\r\nContent-Length: %d\r\n\r\n" % len(_inner) + _inner)
assert _out.count(b"HTTP/1.") == 1 and b" 403 " in _out.split(b"\r\n", 1)[0], _out[:300]
_out = _talk(b"GET /api/does-not-exist HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: %d\r\n\r\n" % len(_inner) + _inner)
assert _out.count(b"HTTP/1.") == 1, "a body left unread must not become the next request"
_srv.shutdown()
print("smuggled request check passed")
