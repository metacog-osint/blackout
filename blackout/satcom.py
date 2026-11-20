"""Satellite networks register (SATCOM lane, ideas#104 item 5): every network found for a satellite operator, and the
lookalikes that share a name, each with a class so the page and the trend series can filter rather than drop them.

The register (`satcom_networks.json`, committed) is compiled by hand from registry records: the RIRs (RIPE DB, ARIN,
APNIC, LACNIC, AFRINIC), PeeringDB, and the operators' own sites for what a network does. Nothing is excluded: a
network that is not satellite internet (AS34602, a Moscow home ISP called Starlink; AS29242, Viasat Ukraine's
satellite-TV company) stays in, classed as such, because a reader searching the name should find it and see why it is
not counted. `confidence` says how far the class was checked.

Classes (closed list, CLASSES): satellite_internet (consumer or enterprise access over satellite),
satellite_maritime_aviation (connectivity sold to ships and aircraft), satellite_operator_other (teleport, capacity,
corporate networks of an operator), satellite_tv (broadcast pay-TV), not_satellite (a lookalike name).

What the lane will read (next steps, not built here): Cloudflare Radar `http/timeseries?asn=` and
`http/top/locations?asn=` per network against its own 28-day normal, IODA per network, and the providers' status
pages. First replay: Starlink (AS14593) located in Ukraine fell from 0.79 to 0.40 of its window peak between 16:00 and
18:00 UTC on 4 Feb 2026, the day after Ukraine's terminal-registration deadline; Starlink worldwide and all Ukrainian
traffic stayed flat.
"""
from __future__ import annotations

import json
from pathlib import Path

FILE = Path(__file__).with_name("satcom_networks.json")
CLASSES = ("satellite_internet", "satellite_maritime_aviation", "satellite_operator_other", "satellite_tv",
           "not_satellite")
COUNTED = ("satellite_internet", "satellite_maritime_aviation")  # what a SATCOM total adds up, by default
CONFIDENCE = ("confirmed", "registry only", "unverified")


def load(path: Path = FILE) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def search(networks: list[dict], text: str = "", classes: tuple | list | None = None) -> list[dict]:
    """Networks whose ASN, operator, registry holder or parent contains `text` (case-insensitive; "AS14593" or
    "14593" both match), limited to `classes` when given. Nothing is dropped from the register itself."""
    t = text.strip().lower().removeprefix("as")
    out = []
    for n in networks:
        if classes and n.get("class") not in classes:
            continue
        hay = " ".join(str(n.get(k) or "") for k in ("asn", "operator", "registry_holder", "parent")).lower()
        if not t or t in hay:
            out.append(n)
    return out


def snapshot_part(doc: dict | None = None) -> dict | None:
    """What the page reads: the whole register with its method, so the page can search and filter it."""
    try:
        doc = doc if doc is not None else load()
    except (OSError, ValueError):
        return None
    return {k: doc.get(k) for k in ("checked_at", "method", "note", "networks", "not_found",
                                    "name_matches_not_satellite")} | {"classes": list(CLASSES),
                                                                                       "counted": list(COUNTED)}
