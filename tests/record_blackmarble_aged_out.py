"""Run as: python -P tests/record_blackmarble_aged_out.py <night> (needs EARTHDATA_TOKEN; it is sent, never printed).

Record, for one night LANCE has dropped while CMR still lists it, the real responses Blackout meets for Kyiv's tile
h21v03: CMR's near-real-time search (listing the granule), LANCE's answer for that granule (404, body as returned),
CMR's standard-product search for the same night, and Kyiv's nights in the last published snapshot (Blackout's own
earlier reads, so the run's first unread night is this one). Adds them to the fixtures' MANIFEST.json."""
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, r"[local path]")
from blackout import blackmarble as bm, feed  # noqa: E402

night = sys.argv[1]
OUT = Path(r"[local path]")
man_path = OUT / "MANIFEST.json"
manifest = json.loads(man_path.read_text(encoding="utf-8"))
responses = manifest.setdefault("responses", {})
at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": bm.USER_AGENT, **(headers or {})})
    try:
        with urllib.request.build_opener(bm._LicenceCheck).open(req, timeout=120) as r:
            return r.status, r.reason, r.headers.get("Content-Type"), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.reason, e.headers.get("Content-Type"), e.read()


def save(name, url, status, reason, ctype, body):
    (OUT / name).write_bytes(body)
    manifest["files"][name] = url
    if status != 200:
        responses[name] = {"status": status, "reason": reason, "content_type": ctype}
    print(name, status, reason, ctype, len(body))


nrt_url = bm.search_url(bm.NRT, night, ["h21v03"])
status, reason, ctype, body = get(nrt_url)
links = bm.granule_links(json.loads(body), bm.NRT)
assert status == 200 and "h21v03" in links, (status, links)
save(f"cmr_{bm.NRT}_{night}_h21v03.json", nrt_url, status, reason, ctype, body)

tile = links["h21v03"]
status, reason, ctype, body = get(tile, {"Authorization": "Bearer " + bm.token()})
assert status == 404, status  # the point of the recording: listed, but gone from LANCE
save("lance_404_" + tile.rsplit("/", 1)[1].replace(".h5", ".json"), tile, status, reason, ctype, body)

std_url = bm.search_url(bm.STANDARD, night, ["h21v03"])
status, reason, ctype, body = get(std_url)
assert status == 200, status
empty = "" if bm.granule_links(json.loads(body), bm.STANDARD) else "_empty"
save(f"cmr_{bm.STANDARD}_{night}_h21v03{empty}.json", std_url, status, reason, ctype, body)

snap_url = f"{feed.SITE}/data/snapshot.json"
status, reason, ctype, body = get(snap_url)
snap = json.loads(body)
kyiv = next(c for c in snap["nightlights"]["cities"] if c["id"] == "kyiv")
extract = {"note": f"Kyiv's nights from Blackout's published snapshot (generated {snap.get('generated_at')}, "
                   f"read {at}), unchanged", "nights": kyiv["nights"]}
(OUT / f"snapshot_kyiv_nights_{at[:10]}.json").write_text(json.dumps(extract, indent=1) + "\n", encoding="utf-8",
                                                          newline="\n")
manifest["files"][f"snapshot_kyiv_nights_{at[:10]}.json"] = snap_url
print("snapshot nights", [n["date"] for n in kyiv["nights"]])
man_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
