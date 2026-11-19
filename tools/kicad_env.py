"""Find the installed KiCad and point SKiDL and the board tools at it. Import it before skidl.

Looks at KICAD_DIR first, then the usual install places on Windows, macOS and Linux. Sets KICADn_SYMBOL_DIR and
KICADn_FOOTPRINT_DIR for SKiDL, and exposes KICAD_CLI and KICAD_PYTHON for tools/pcb.py.
"""
import glob
import os
import shutil
import sys


def _find():
    cands = [os.environ.get("KICAD_DIR", "")]
    if sys.platform == "win32":
        for base in (os.path.expandvars(r"%LOCALAPPDATA%\Programs\KiCad"), r"C:\Program Files\KiCad"):
            cands += sorted(glob.glob(os.path.join(base, "*")), reverse=True)
    elif sys.platform == "darwin":
        cands += ["/Applications/KiCad/KiCad.app/Contents"]
    else:
        cands += ["/usr", "/usr/local"]
    for c in cands:
        if not c:
            continue
        cli = shutil.which("kicad-cli", path=os.path.join(c, "bin")) or shutil.which("kicad-cli", path=os.path.join(c, "MacOS"))
        share = next((s for s in (os.path.join(c, "share", "kicad"), os.path.join(c, "SharedSupport")) if os.path.isdir(os.path.join(s, "symbols"))), None)
        if cli and share:
            return c, cli, share
    return None, shutil.which("kicad-cli"), None


KICAD_DIR, KICAD_CLI, KICAD_SHARE = _find()
KICAD_PYTHON = None
if KICAD_DIR:
    for p in (os.path.join(KICAD_DIR, "bin", "python.exe"), os.path.join(KICAD_DIR, "Frameworks", "Python.framework", "Versions", "Current", "bin", "python3")):
        if os.path.exists(p):
            KICAD_PYTHON = p
if KICAD_SHARE:
    for v in ("", "6", "7", "8", "9", "10"):
        os.environ.setdefault(f"KICAD{v}_SYMBOL_DIR", os.path.join(KICAD_SHARE, "symbols"))
        os.environ.setdefault(f"KICAD{v}_FOOTPRINT_DIR", os.path.join(KICAD_SHARE, "footprints"))
        os.environ.setdefault(f"KICAD{v}_3DMODEL_DIR", os.path.join(KICAD_SHARE, "3dmodels"))

if __name__ == "__main__":
    print("KiCad:", KICAD_DIR, "\ncli:", KICAD_CLI, "\nlibraries:", KICAD_SHARE, "\npython:", KICAD_PYTHON)
