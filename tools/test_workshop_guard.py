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
assert "start it" in guard(Req("POST", b"x"), "/server/files/upload?print=true")
assert guard(Req("POST", upload_print.replace(b'name="print"\r\n\r\ntrue', b'name="root"\r\n\r\ngcodes')), "/server/files/upload") is None

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
