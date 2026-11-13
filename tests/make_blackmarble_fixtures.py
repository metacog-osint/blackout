"""Run as: python -P tests/make_blackmarble_fixtures.py <folder with the full .h5 tiles>.

Crop real VJ146A2 h21v03 tiles to Kyiv's box (values unchanged) and save the real CMR responses, as fixtures."""
import glob
import json
import sys
import urllib.request
from pathlib import Path

import h5py
import numpy as np

sys.path.insert(0, r"[local path]")
from blackout import blackmarble as bm  # noqa: E402

SRC = Path(sys.argv[1])
OUT = Path(r"[local path]")
OUT.mkdir(parents=True, exist_ok=True)
kyiv = next(c for c in bm.CITIES if c["id"] == "kyiv")
s, n, w, e = bm.box(kyiv)
manifest = {"note": "", "files": {}}
for night in ("2026-10-17", "2026-10-21", "2026-10-25"):
    url = bm.search_url(bm.STANDARD, night, ["h21v03"])
    raw = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": bm.USER_AGENT}), timeout=60).read()
    doc = json.loads(raw)
    name = f"cmr_{bm.STANDARD}_{night}_h21v03.json"
    (OUT / name).write_bytes(raw)
    manifest["files"][name] = url
    link = bm.granule_links(doc, bm.STANDARD)["h21v03"]
    src = Path(glob.glob(str(SRC / ("*" + link.rsplit("/", 1)[1])))[0])
    cropped = "kyiv_" + src.name
    with h5py.File(src, "r") as fi, h5py.File(OUT / cropped, "w") as fo:
        for k, v in fi.attrs.items():
            fo.attrs[k] = v
        d = fi[bm.FIELDS]
        lat, lon = d["lat"][:], d["lon"][:]
        r = np.nonzero((lat >= s - 0.02) & (lat < n + 0.02))[0]
        c = np.nonzero((lon >= w - 0.02) & (lon < e + 0.02))[0]
        rs, cs = slice(int(r[0]), int(r[-1]) + 1), slice(int(c[0]), int(c[-1]) + 1)
        g = fo.create_group(bm.FIELDS)
        for name_ds in ("DNB_BRDF-Corrected_NTL", "Mandatory_Quality_Flag", "QF_Cloud_Mask", "lat", "lon"):
            ds = d[name_ds]
            data = ds[rs] if name_ds == "lat" else ds[cs] if name_ds == "lon" else ds[rs, cs]
            out = g.create_dataset(name_ds, data=data, compression="gzip" if data.ndim == 2 else None)
            for k, v in ds.attrs.items():
                out.attrs[k] = v
        print(night, cropped, "rows", r[0], r[-1], "cols", c[0], c[-1])
    manifest["files"][cropped] = link
    with h5py.File(src, "r") as full, h5py.File(OUT / cropped, "r") as small:
        assert bm.box_sums(full, bm.box(kyiv)) == bm.box_sums(small, bm.box(kyiv)), night
manifest["note"] = ("Real responses recorded 13 Nov 2026. The cmr_*.json files are NASA CMR's granule searches as "
                    "returned. The kyiv_*.h5 files are the real VJ146A2 h21v03 tiles (LAADS, Earthdata Cloud) cropped "
                    "to Kyiv's box plus 0.02 degrees by tests' bm_fixtures script: the five datasets Blackout reads "
                    "(DNB_BRDF-Corrected_NTL, Mandatory_Quality_Flag, QF_Cloud_Mask, lat, lon) and every attribute, "
                    "values unchanged; box_sums on each equals box_sums on the full tile. The full tiles (11-33 MB) "
                    "are not kept. The URL is the full tile's.")
(OUT / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
print(sorted(p.name for p in OUT.iterdir()), sum(p.stat().st_size for p in OUT.iterdir()))
