"""PCB trace width for a current, from the IPC-2221 chart formula.

    python trace_width.py 2.5                 # amps; outer layer, 1 oz copper, 10 C rise
    python trace_width.py 2.5 --rise 20 --oz 2 --inner

Prints the minimum width in mm and mils. Use at least the board house's minimum (often 0.127 mm) and round up:
the formula is a chart fit, not a guarantee. Short runs to a connector can be wider than this, never narrower.
"""
import argparse


def width_mm(amps, rise=10.0, oz=1.0, inner=False):
    k = 0.024 if inner else 0.048
    area_mils2 = (amps / (k * rise ** 0.44)) ** (1 / 0.725)   # cross-section, square mils
    thickness_mils = 1.378 * oz                               # 1 oz/ft2 copper is about 1.378 mil thick
    return area_mils2 / thickness_mils * 0.0254


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("amps", type=float)
    ap.add_argument("--rise", type=float, default=10.0, help="allowed temperature rise, C")
    ap.add_argument("--oz", type=float, default=1.0, help="copper weight, oz per square foot")
    ap.add_argument("--inner", action="store_true", help="inner layer (half the cooling)")
    a = ap.parse_args()
    w = width_mm(a.amps, a.rise, a.oz, a.inner)
    print(f"{a.amps} A, {a.rise:g} C rise, {a.oz:g} oz, {'inner' if a.inner else 'outer'}: "
          f"at least {w:.3f} mm ({w / 0.0254:.1f} mil)")


if __name__ == "__main__":
    # check against the published chart: 1 A, 10 C, 1 oz outer is about 0.30 mm (12 mil)
    assert 0.27 < width_mm(1.0) < 0.33, width_mm(1.0)
    main()
