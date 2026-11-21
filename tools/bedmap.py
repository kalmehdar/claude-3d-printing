"""Top-down pictures of the print bed as PNG, standard library only.

    first_layer(lines)            -> extrusion paths of the first layer from G-code lines
    render(bed_mm, paths, parts, mark=None, done=()) -> PNG bytes

Used by Workshop for the "cancel one part" picture (each part's outline, the one to cancel filled red) and the
first-layer check (what the slicer planned for layer 1, sent to Claude beside the camera frame).
"""
import re
import struct
import zlib

SIZE = 540   # pixels per side
BG, GRID, PATH, PART, MARK, DONE = (18, 19, 22), (40, 42, 48), (124, 128, 240), (150, 153, 160), (235, 93, 93), (70, 72, 78)


def first_layer(lines):
    """[[(x, y), ...], ...] extrusion paths of the first printed layer: everything between the first and second
    ;LAYER_CHANGE markers (OrcaSlicer writes them), so the purge line before layer 1 and the Z hops are left out."""
    paths, cur, x, y, e, rel_e, layers = [], [], 0.0, 0.0, 0.0, False, 0
    for raw in lines:
        if raw.startswith(";LAYER_CHANGE"):
            layers += 1
            if layers > 1:
                break
            continue
        line = raw.split(";", 1)[0].strip()
        if line == "M83":
            rel_e = True
        elif line == "M82":
            rel_e = False
        if not line.startswith(("G0", "G1")) or line.startswith(("G10", "G11")):
            continue
        w = dict((m[0], float(m[1:])) for m in re.findall(r"[XYE]-?\d*\.?\d+", line))
        nx, ny = w.get("X", x), w.get("Y", y)
        extruding = "E" in w and ((w["E"] > 0) if rel_e else (w["E"] > e))
        if "E" in w and not rel_e:
            e = w["E"]
        if layers == 1 and extruding and (nx, ny) != (x, y):
            cur = cur or [(x, y)]
            cur.append((nx, ny))
        elif len(cur) > 1 and (nx, ny) != (x, y):
            paths.append(cur)
            cur = []
        x, y = nx, ny
    if len(cur) > 1:
        paths.append(cur)
    return paths


def _png(w, h, px):
    rows = b"".join(b"\0" + bytes(px[y * w * 3:(y + 1) * w * 3]) for y in range(h))
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(rows, 6)) + chunk(b"IEND", b"")


def render(bed_mm, paths=(), parts=(), mark=None, done=()):
    """PNG of the bed seen from above, front edge at the bottom. paths: first-layer lines; parts: [{name, polygon}];
    mark: the part name filled red; done: names already cancelled (drawn dim)."""
    n, s = SIZE, SIZE / float(bed_mm)
    px = bytearray(BG * (n * n))

    def put(x, y, c):
        if 0 <= x < n and 0 <= y < n:
            i = (y * n + x) * 3
            px[i:i + 3] = bytes(c)

    def to_px(p):
        return int(p[0] * s), int(n - 1 - p[1] * s)   # bed Y grows away from you: flip so the front is at the bottom

    def line(a, b, c, thick=1):
        (x0, y0), (x1, y1) = to_px(a), to_px(b)
        steps = max(abs(x1 - x0), abs(y1 - y0), 1)
        for i in range(steps + 1):
            x, y = round(x0 + (x1 - x0) * i / steps), round(y0 + (y1 - y0) * i / steps)
            for dx in range(thick):
                for dy in range(thick):
                    put(x + dx, y + dy, c)

    def blend(x, y, c, a):
        if 0 <= x < n and 0 <= y < n:
            i = (y * n + x) * 3
            px[i:i + 3] = bytes(round(px[i + k] * (1 - a) + c[k] * a) for k in range(3))

    def fill(poly, c, a=1.0):   # even-odd scanline fill in pixel space; a < 1 lets what is underneath show
        pts = [to_px(p) for p in poly]
        for y in range(max(0, min(p[1] for p in pts)), min(n, max(p[1] for p in pts) + 1)):
            xs = sorted(x0 + (y - y0) * (x1 - x0) / (y1 - y0) for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1])
                        if (y0 <= y < y1) or (y1 <= y < y0))
            for l, r in zip(xs[::2], xs[1::2]):
                for x in range(int(l), int(r) + 1):
                    blend(x, y, c, a)

    for g in range(0, int(bed_mm) + 1, 50):   # a 50 mm grid
        line((g, 0), (g, bed_mm), GRID)
        line((0, g), (bed_mm, g), GRID)
    for p in paths:   # the first layer: blue, or dim grey when a part is marked so the red stands out
        for a, b in zip(p, p[1:]):
            line(a, b, (78, 80, 92) if mark else PATH, 2)
    for part in parts:
        if part["name"] == mark and len(part.get("polygon") or []) >= 3:
            fill([tuple(q) for q in part["polygon"]], MARK, 0.7)
    for part in parts:
        poly = [tuple(q) for q in part.get("polygon") or []]
        if len(poly) < 3:
            continue
        colour = DONE if part["name"] in done else MARK if part["name"] == mark else PART
        for a, b in zip(poly, poly[1:] + poly[:1]):
            line(a, b, colour, 2)
    return _png(n, n, px)


if __name__ == "__main__":   # self-check: a square first layer and two parts, the right one marked
    g = ["M83", "G1 X0 Y1 E5", "G1 X50 Y1 E5", "G1 Z5", ";LAYER_CHANGE", "G1 Z0.25", "G1 X10 Y10", "G1 X60 Y10 E2", "G1 X60 Y60 E2", "G0 X100 Y100",
         "G1 X120 Y100 E1", ";LAYER_CHANGE", "G1 Z0.45", "G1 X200 Y200 E5"]
    p = first_layer(g)
    assert p == [[(10, 10), (60, 10), (60, 60)], [(100, 100), (120, 100)]], p
    absolute = first_layer(["M82", ";LAYER_CHANGE", "G1 Z0.2 X0 Y0 E0", "G1 Z0.6", "G1 Z0.2", "G1 X5 Y0 E1", "G1 X5 Y5 E1", "G1 X9 Y9 E0.5"])
    assert absolute == [[(0, 0), (5, 0)]], absolute   # E not rising = travel, not plastic
    img = render(270, p, [{"name": "a", "polygon": [[10, 10], [60, 10], [60, 60], [10, 60]]},
                          {"name": "b", "polygon": [[100, 100], [150, 100], [150, 150], [100, 150]]}], mark="b")
    assert img[:8] == b"\x89PNG\r\n\x1a\n" and len(img) > 500
    w, h = struct.unpack(">II", img[16:24])
    raw = zlib.decompress(img[img.index(b"IDAT") + 4:img.index(b"IEND") - 8])
    def at(x, y):
        i = y * (w * 3 + 1) + 1 + x * 3
        return tuple(raw[i:i + 3])
    cx = int(125 * SIZE / 270); cy = int(SIZE - 1 - 125 * SIZE / 270)
    red = lambda c: c[0] > c[1] + 80 and c[0] > c[2] + 80
    assert red(at(cx, cy)), at(cx, cy)   # inside part b: filled red
    ax = int(35 * SIZE / 270); ay = int(SIZE - 1 - 35 * SIZE / 270)
    assert not red(at(ax, ay)), at(ax, ay)   # part a is not
    print("bedmap checks passed")
