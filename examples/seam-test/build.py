"""Seam test plate: four numbered cylinders, each with its own seam settings as per-object settings in one 3MF.
python examples/seam-test/build.py <out.3mf> <stl dir with seam1..4.stl>  (export the STLs from seam-cylinder.scad, n=1..4)"""
import json, os, sys, uuid, zipfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools"))
import make_3mf  # noqa: E402

# Khalid's 22 Nov seam test picked a plain seam at the back over scarf seams; this round goes after the zits that remain.
VARIANTS = {
    1: {},                                                                    # baseline: quality as it is (back, no scarf)
    2: {"seam_gap": "15%", "wipe_on_loops": "1"},                             # stop a little short of the start, wipe inward
    3: {"seam_position": "aligned", "seam_gap": "10%", "wipe_before_external_loop": "1"},
    4: {"seam_slope_type": "external", "seam_slope_conditional": "0", "seam_slope_steps": "10", "seam_slope_start_height": "0"},
}


def build(out, stl_dir, extruder=1):
    objs, items, cfg = [], [], []
    for n, settings in VARIANTS.items():
        vs, ts = make_3mf.read_stl(os.path.join(stl_dir, f"seam{n}.stl"))
        vx = "".join(f'<vertex x="{v[0]:.4f}" y="{v[1]:.4f}" z="{v[2]:.4f}"/>' for v in vs)
        tx = "".join(f'<triangle v1="{t[0]}" v2="{t[1]}" v3="{t[2]}"/>' for t in ts)
        objs.append(f'<object id="{n}" p:UUID="{uuid.uuid4()}" type="model"><mesh><vertices>{vx}</vertices><triangles>{tx}</triangles></mesh></object>')
        x, y = 135 + (n - 2.5) * 34, 135
        items.append(f'<item objectid="{n}" p:UUID="{uuid.uuid4()}" transform="1 0 0 0 1 0 0 0 1 {x} {y} 0" printable="1"/>')
        meta = "".join(f'\n    <metadata key="{k}" value="{v}"/>' for k, v in {"name": f"seam {n}", "extruder": extruder, **settings}.items())
        cfg.append(f'  <object id="{n}">{meta}\n    <part id="{n}" subtype="normal_part"><metadata key="name" value="seam {n}"/></part>\n  </object>')
    model = ('<?xml version="1.0" encoding="UTF-8"?>\n<model unit="millimeter" xml:lang="en-US" '
             'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
             'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06" requiredextensions="p">\n'
             ' <metadata name="Application">print-bench seam-test</metadata>\n <resources>\n  ' + "\n  ".join(objs) +
             f'\n </resources>\n <build p:UUID="{uuid.uuid4()}">' + "".join(items) + '</build>\n</model>\n')
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?>\n<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/><Default Extension="config" ContentType="text/xml"/></Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Target="/3D/3dmodel.model" Id="rel-1" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
        z.writestr("3D/3dmodel.model", model)
        z.writestr("Metadata/model_settings.config", '<?xml version="1.0" encoding="UTF-8"?>\n<config>\n' + "\n".join(cfg) + "\n</config>\n")
        z.writestr("Metadata/project_settings.config", json.dumps({"filament_colour": ["#E2DEDB"]}))


if __name__ == "__main__":
    build(sys.argv[1], sys.argv[2])
