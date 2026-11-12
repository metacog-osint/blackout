"""Provider status feeds (BO-WP-3, BO-IND-7): open incidents as each provider reports them on its own status page.

Blackout republishes what the provider says (title, times, affected areas, link) as a reported observation and never
assesses a cause from it. Shapes checked against real responses (tests/fixtures/status/MANIFEST.json):

  AWS        status.aws.amazon.com/rss/all.rss. One RSS item per update; the guid fragment is
             "<service>-<region>_<epoch>". An event is open while its latest update's title does not start with
             "Service is operating normally". The feed keeps every update of an open event (back to 1 Mar 2026 for
             me-central-1 on 12 Nov 2026), so the first update in the feed is the event's start; AWS's own
             current-events list gave the same start that day.
  Google     status.cloud.google.com/incidents.json. An incident is open while it has no "end".
  Azure      azure.status.microsoft/en-us/status/feed/. Lists only current incidents; empty when none.
  M365       status.cloud.microsoft/api/posts/mac, the JSON behind Microsoft's public status page (added 12 Nov 2026,
             after that day's Microsoft 365 outage reached no feed Blackout read). One current post; Status
             "Available" when nothing is posted. Microsoft uses the page for issues that stop admins reaching their
             tenant's Service health, so smaller incidents never appear here. api/posts/m365Consumer answers 501.
  Starlink   starlink.statuspage.io/api/v2/summary.json (Atlassian Statuspage v2; "incidents" holds only unresolved
             ones). Who runs this page is unverified: its whole history holds one incident (April 2022), so it
             missed Starlink's global outage of 24 Jul 2025. Its silence means nothing; AS14593 on IODA is the check.

A feed that can't be read is listed in problems and publishes nothing; its incidents are never carried over or
filled in.
"""
from __future__ import annotations

import html
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

USER_AGENT = "Blackout/0.2 (+https://theintelligence.company)"

FEEDS = [
    {"id": "aws", "source_id": "aws-health", "provider": "amazon", "label": "AWS", "publisher": "Amazon Web Services",
     "title": "AWS Health Dashboard RSS (all services)", "url": "https://status.aws.amazon.com/rss/all.rss",
     "page": "https://health.aws.amazon.com/health/status", "format": "aws_rss", "grade": "B2"},
    {"id": "gcp", "source_id": "gcp-status", "provider": "google", "label": "Google Cloud", "publisher": "Google Cloud",
     "title": "Google Cloud Service Health incidents", "url": "https://status.cloud.google.com/incidents.json",
     "page": "https://status.cloud.google.com/", "format": "gcp_json", "grade": "B2"},
    {"id": "azure", "source_id": "azure-status", "provider": "microsoft", "label": "Azure", "publisher": "Microsoft Azure",
     "title": "Azure status RSS (current incidents)", "url": "https://azure.status.microsoft/en-us/status/feed/",
     "page": "https://azure.status.microsoft/en-us/status/", "format": "azure_rss", "grade": "B2"},
    {"id": "m365", "source_id": "m365-status", "provider": "microsoft", "label": "Microsoft 365",
     "publisher": "Microsoft 365", "title": "Microsoft 365 public service status (status.cloud.microsoft)",
     "url": "https://status.cloud.microsoft/api/posts/mac", "page": "https://status.cloud.microsoft/",
     "format": "mscloud_json", "grade": "B2",
     "note": "Only what Microsoft posts publicly: the page is for issues that stop admins reaching their tenant's "
             "Service health, where smaller incidents are listed. Nothing open here doesn't mean nothing is wrong."},
    {"id": "starlink", "source_id": "starlink-statuspage", "provider": "starlink", "label": "Starlink", "publisher": "starlink.statuspage.io (operator unverified)",
     "title": "Starlink page on Atlassian Statuspage", "url": "https://starlink.statuspage.io/api/v2/summary.json",
     "page": "https://starlink.statuspage.io/", "format": "statuspage", "grade": "F6",
     "note": "Operator unverified; one incident in its whole history (April 2022), none for the 24 Jul 2025 global "
             "outage. No listed incident means nothing here; watch AS14593 instead."},
]

FEED_BY_ID = {f["id"]: f for f in FEEDS}
# One name per provider for BO-IND-8, whichever of its feeds or networks reported (Azure or Microsoft 365).
PROVIDER_LABELS = {"amazon": "Amazon", "google": "Google", "microsoft": "Microsoft", "starlink": "Starlink"}

CONCURRENT_HOURS = 2     # BO-IND-8: onsets at different providers within 2 h of each other ...
RECENT_HOURS = 24        # ... both inside the last 24 h

AWS_NORMAL = "service is operating normally"
AWS_REGION_RE = re.compile(r"^(?P<service>.+?)-(?P<region>(?:[a-z]{2}|eusc)(?:-gov|-iso[a-z]*)?-[a-z]+-\d+)$")
STATUSPAGE_CLOSED = {"resolved", "postmortem", "completed"}


def _utc(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def _parse_time(s) -> datetime | None:
    """RFC 822 (RSS pubDate, incl. PST/PDT and Z) or ISO 8601. None if it can't be read."""
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = parsedate_to_datetime(s)
        except (TypeError, ValueError, IndexError):
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _xml(body: bytes) -> ET.Element:
    """Parse a feed, refusing DTDs and entity declarations (a status feed has no use for them)."""
    head = body[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in body.lower():
        raise ValueError("feed declares a DTD or entities; refused")
    return ET.fromstring(body)


def _text(el, tag: str) -> str:
    x = el.find(tag)
    return (x.text or "").strip() if x is not None and x.text else ""


def parse_aws_rss(body: bytes) -> list[dict]:
    """Open AWS events: per service-region key, the updates since the last 'operating normally' one."""
    by_key: dict[str, list[dict]] = {}
    for it in _xml(body).iter("item"):
        guid = _text(it, "guid")
        frag = guid.rsplit("#", 1)[-1]
        key, _, ts = frag.rpartition("_")
        when = _parse_time(_text(it, "pubDate"))
        if when is None and ts.isdigit():
            when = datetime.fromtimestamp(int(ts), tz=timezone.utc)
        if not key or when is None:
            continue
        by_key.setdefault(key, []).append({"title": _text(it, "title"), "when": when,
                                           "link": _text(it, "link") or "https://status.aws.amazon.com/"})
    out = []
    for key, ups in by_key.items():
        ups.sort(key=lambda u: u["when"])
        last_normal = max((i for i, u in enumerate(ups) if u["title"].lower().startswith(AWS_NORMAL)), default=-1)
        run = ups[last_normal + 1:]
        if not run:
            continue
        m = AWS_REGION_RE.match(key)
        service, region = (m.group("service"), m.group("region")) if m else (key, None)
        status, _, headline = run[-1]["title"].partition(": ")
        out.append({
            "feed": "aws", "incident_id": f"{key}:{int(run[0]['when'].timestamp())}",
            "title": headline or run[-1]["title"], "status": status if headline else None,
            "service": "Multiple services" if service == "multipleservices" else service,
            "locations": [region] if region else [],
            "started_at": _utc(run[0]["when"]), "updated_at": _utc(run[-1]["when"]), "updates": len(run),
            "link": run[-1]["link"],
        })
    return out


def parse_gcp_json(body: bytes) -> list[dict]:
    """Open Google Cloud incidents: those with no end."""
    data = json.loads(body)
    if not isinstance(data, list):
        raise ValueError("incidents.json is not a list")
    out = []
    for inc in data:
        if not isinstance(inc, dict) or inc.get("end") or not inc.get("id"):
            continue
        mru = inc.get("most_recent_update") or {}
        locs = inc.get("currently_affected_locations") or mru.get("affected_locations") or []
        prods = [p.get("title") for p in inc.get("affected_products") or [] if isinstance(p, dict) and p.get("title")]
        uri = str(inc.get("uri") or f"incidents/{inc['id']}")
        out.append({
            "feed": "gcp", "incident_id": str(inc["id"]),
            "title": (inc.get("external_desc") or "").strip().split("\n")[0][:200],
            "status": inc.get("status_impact"), "severity": inc.get("severity"),
            "service": inc.get("service_name"), "products": prods,
            "locations": [l.get("id") for l in locs if isinstance(l, dict) and l.get("id")],
            "started_at": _utc(_parse_time(inc.get("begin"))),
            "updated_at": _utc(_parse_time(mru.get("when") or inc.get("modified"))),
            "updates": len(inc.get("updates") or []),
            "link": "https://status.cloud.google.com/" + uri.lstrip("/"),
        })
    return out


def parse_azure_rss(body: bytes) -> list[dict]:
    """Current Azure incidents: every item in the feed. Categories list services, then regions, with nothing to tell
    where one list ends, so they are kept together as Azure gives them ("affected")."""
    out = []
    for it in _xml(body).iter("item"):
        title = _text(it, "title")
        cats = [(c.text or "").strip() for c in it.findall("category") if c.text and c.text.strip()]
        out.append({
            "feed": "azure", "incident_id": _text(it, "guid") or title,
            "title": html.unescape(title), "status": None,
            "service": None, "affected": cats, "locations": [],
            "started_at": _utc(_parse_time(_text(it, "pubDate"))), "updated_at": None, "updates": None,
            "link": _text(it, "link") or "https://azure.status.microsoft/en-us/status/",
        })
    return out


def parse_statuspage(body: bytes) -> list[dict]:
    """Unresolved incidents from an Atlassian Statuspage v2 summary.json."""
    data = json.loads(body)
    incs = data.get("incidents") if isinstance(data, dict) else None
    if not isinstance(incs, list):
        raise ValueError("summary.json has no incidents list")
    page = ((data.get("page") or {}).get("url") or "").rstrip("/")
    out = []
    for inc in incs:
        if not isinstance(inc, dict) or not inc.get("id") or str(inc.get("status")).lower() in STATUSPAGE_CLOSED:
            continue
        comps = [c.get("name") for c in inc.get("components") or [] if isinstance(c, dict) and c.get("name")]
        out.append({
            "feed": "statuspage", "incident_id": str(inc["id"]), "title": (inc.get("name") or "").strip(),
            "status": inc.get("status"), "severity": inc.get("impact"), "service": None, "products": comps,
            "locations": [], "started_at": _utc(_parse_time(inc.get("started_at") or inc.get("created_at"))),
            "updated_at": _utc(_parse_time(inc.get("updated_at"))),
            "updates": len(inc.get("incident_updates") or []),
            "link": inc.get("shortlink") or (f"{page}/incidents/{inc['id']}" if page else ""),
        })
    return out


MSCLOUD_CLOSED = {"", "available", "service operational", "service restored", "false positive",
                  "post-incident report published"}


def _html_lines(s: str) -> list[str]:
    """Plain-text lines from a status post's HTML (div, p, li and br end a line; tags dropped; entities decoded)."""
    s = re.sub(r"(?i)<\s*(br|/div|div|/p|p|li|/li)\b[^>]*>", "\n", s or "")
    s = html.unescape(re.sub(r"<[^>]+>", "", s)).replace("\xa0", " ")
    return [ln.strip() for ln in s.split("\n") if ln.strip()]


def parse_mscloud_json(body: bytes) -> list[dict]:
    """Microsoft 365's public status post (status.cloud.microsoft/api/posts/mac): one current post, open unless its
    Status is Available or a closing status (Microsoft's status definitions, learn.microsoft.com). The post gives no
    start time (AuthoredDateTime is its latest update), so started_at stays None. Affected services are the "- "
    lines of the message, each cut at its first colon. A post with no Status is a changed shape and is refused."""
    data = json.loads(body)
    posts = data if isinstance(data, list) else [data]
    if not all(isinstance(p, dict) and "Status" in p for p in posts):
        raise ValueError("status post has no Status field")
    out = []
    for p in posts:
        status = str(p.get("Status") or "").strip()
        title = str(p.get("Title") or "").strip()
        if status.lower() in MSCLOUD_CLOSED or not title or not p.get("Id"):
            continue
        affected = []
        for ln in _html_lines(str(p.get("Message") or "")):
            if ln.startswith("- "):
                name = ln[2:].split(":", 1)[0].strip()[:80]
                if name and name not in affected:
                    affected.append(name)
        out.append({
            "feed": "m365", "incident_id": str(p["Id"]), "title": html.unescape(title)[:200], "status": status,
            "service": None, "affected": affected, "locations": [], "started_at": None,
            "updated_at": _utc(_parse_time(p.get("LastUpdatedTime") or p.get("AuthoredDateTime"))), "updates": None,
            "link": "https://status.cloud.microsoft/",
        })
    return out


PARSERS = {"aws_rss": parse_aws_rss, "gcp_json": parse_gcp_json, "azure_rss": parse_azure_rss,
           "mscloud_json": parse_mscloud_json, "statuspage": parse_statuspage}


def _get(url: str, timeout: float = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(20_000_000)


def fetch_status_feeds(feeds: list[dict] | None = None, fetch=_get) -> tuple[list[dict], list[dict], list[dict]]:
    """(open incidents, per-feed read status, problems). A feed that fails is read=False with no incidents."""
    incidents, status, problems = [], [], []
    for f in feeds or FEEDS:
        st = {k: f.get(k) for k in ("id", "provider", "label", "publisher", "url", "page", "grade", "note")}
        try:
            found = PARSERS[f["format"]](fetch(f["url"]))
        except Exception as exc:
            problems.append({"feed": f["id"], "url": f["url"], "error": f"{type(exc).__name__}: {exc}"[:200]})
            status.append({**st, "read": False, "open": None})
            continue
        for inc in found:
            inc.update({"feed": f["id"], "provider": f["provider"], "provider_label": f["label"]})
        incidents += found
        status.append({**st, "read": True, "open": len(found)})
    incidents.sort(key=lambda i: (i.get("started_at") or "", i["feed"], i["incident_id"]), reverse=True)
    return incidents, status, problems


def concurrency(asn_records: list[dict], incidents: list[dict], generated_at: str) -> dict | None:
    """BO-IND-8: the largest set of different providers whose drops (BO-IND-6) or reported incidents (BO-IND-7) began
    within CONCURRENT_HOURS of each other, inside the last RECENT_HOURS. None unless two or more providers qualify.
    An onset that isn't known (no start time) is left out, never guessed."""
    now = _parse_time(generated_at)
    if now is None:
        return None
    onsets = []
    for w in asn_records or []:
        t = _parse_time(w.get("window_start")) if w.get("indicator_id") == "BO-IND-6" else None
        if t:
            onsets.append({"provider": w["provider"], "label": f"{w['label']} {w['asn']} (IODA)", "t": t,
                           "feed": None, "link": None})
    for inc in incidents or []:
        t = _parse_time(inc.get("started_at"))
        if t:
            onsets.append({"provider": inc["provider"], "label": f"{inc['provider_label']} status: {inc['title']}",
                           "t": t, "feed": inc["feed"], "link": inc.get("link")})
    recent = sorted((o for o in onsets if 0 <= (now - o["t"]).total_seconds() <= RECENT_HOURS * 3600),
                    key=lambda o: o["t"])
    best = None
    for i, o in enumerate(recent):
        group = [x for x in recent[i:] if (x["t"] - o["t"]).total_seconds() <= CONCURRENT_HOURS * 3600]
        provs = sorted({x["provider"] for x in group})
        if len(provs) >= 2 and (best is None or len(provs) > len(best[1])):
            best = (group, provs)
    if not best:
        return None
    group, provs = best
    caution = None
    if all(x["feed"] is None for x in group):
        # Only IODA drops and no provider reporting anything: an IODA collection fault looks the same (BO-H-3D).
        caution = ("Every onset here is an IODA drop and no provider lists an incident; a fault in IODA's own "
                   "collection would look the same (BO-H-3D).")
    return {
        "indicator_id": "BO-IND-8",
        "caution": caution,
        "providers": provs,
        "provider_labels": [PROVIDER_LABELS.get(p, p) for p in provs],
        "window_start": _utc(group[0]["t"]),
        "window_end": _utc(group[-1]["t"]),
        "onsets": [{"provider": x["provider"], "label": x["label"], "onset": _utc(x["t"]), "feed": x["feed"],
                    "link": x["link"]} for x in group],
    }
