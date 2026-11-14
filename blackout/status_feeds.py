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

Added 13 Nov 2026 (after the Microsoft 365 outage of 12 Nov and Apple's iCloud outage of 10 Nov reached no feed
Blackout read):

  Cloudflare www.cloudflarestatus.com/api/v2/incidents/unresolved.json (Statuspage v2, open incidents only). Kept
             only when Cloudflare rates it major or critical, or it lists a core network component (CLOUDFLARE_CORE:
             Network, CDN/Cache, Authoritative DNS, Recursive DNS, which Cloudflare describes as "Free recursive
             DNS service at 1.1.1.1 and 1.0.0.1", DNS Root Servers, WARP, Magic Transit, China Network) or a data
             centre ("City, Country - (IATA)"). Why both: Cloudflare rates nearly everything minor (47 of its last
             50 incidents on 13 Nov 2026, the other 3 none), including network ones ("Network Performance Issues
             in APJC", 3 Nov 2026, component Network), so impact alone would drop the incidents Blackout exists
             for; a component rule alone would drop a global outage filed under developer products. Left out:
             developer-platform incidents (on 13 Nov 2026 all 3 open ones: Durable Objects, Replicate, Workers).
  Workspace  www.google.com/appsstatus/dashboard/incidents.json: Google Cloud's incidents.json shape, so the same
             rule (open while it has no end); links go to www.google.com/appsstatus/dashboard/. None of 53 distinct
             Wayback captures (2021-2026) holds an open incident, so the open path is the Google Cloud one.
  Apple      www.apple.com/support/systemstatus/data/system_status_en_US.js (plain JSON despite .js): consumer
             services, each with events. Open = eventStatus not resolved, completed or upcoming, and statusType
             not Maintenance (values seen in 115 Wayback captures, 2024-2026: resolved, ongoing, completed,
             upcoming; Outage, Issue, Performance, Maintenance). One outage is posted on every service it hit
             under one messageId, so events are grouped by messageId into one incident listing the services; its
             type is the worst listed (Outage, then Issue, then Performance). Times: epochStartDate (ms), else
             startDate like "11/10/2026 14:49 PST" read as US Pacific (PST UTC-8, PDT UTC-7). Apple's website
             Terms of Use bar republishing site content without consent, so only facts are kept (services, type,
             times) and Apple's message text is never quoted.
  Akamai     www.akamaistatus.com/history.rss, because akamaistatus.com's robots.txt disallows /api/. Statuspage's
             history RSS: one item per incident, its updates newest first, each "<small>Mon D, HH:MM ZONE</small>
             <br><strong>Status</strong>". Open = the newest update's status is not Resolved, Postmortem or
             Completed; an item with a Scheduled, In progress, Verifying or Completed update is a maintenance and
             is left out; "Customer Security Advisory" items are left out. On 13 Nov 2026 this gave the same two
             open incidents as the unresolved list on Akamai's own status page. The feed holds the 25 newest
             items, so an incident older than those would be missed.
  Slack      slack-status.com/api/v2.0.0/current (status.slack.com 301s there): active_incidents, open unless
             status is resolved or a maintenance status (scheduled, completed, cancelled), per Slack's API docs
             (docs.slack.dev/reference/slack-status-api, read 13 Nov 2026). No capture with an open incident exists;
             field names are checked against Slack's own history endpoint.
  Zoom       www.zoomstatus.com/history.rss (status.zoom.us 302s to zoomstatus.com, whose robots.txt also
             disallows /api/), read like Akamai's. Zoom's maintenance notices fill most of its 25 items (they
             reached back only to 6 Nov 2026 on 13 Nov), so an incident open longer than about a week would be
             missed. Zoom's summary.json, read once on 13 Nov 2026, listed the same one open incident.

A feed that can't be read is listed in problems and publishes nothing; its incidents are never carried over or
filled in.
"""
from __future__ import annotations

import html
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
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
    {"id": "cloudflare", "source_id": "cloudflare-status", "provider": "cloudflare", "label": "Cloudflare",
     "publisher": "Cloudflare", "title": "Cloudflare Status unresolved incidents (Atlassian Statuspage)",
     "url": "https://www.cloudflarestatus.com/api/v2/incidents/unresolved.json",
     "page": "https://www.cloudflarestatus.com/", "format": "cloudflare_json", "grade": "B2",
     "note": "Lists only incidents on Cloudflare's network, CDN, DNS (1.1.1.1 included), WARP or a data centre, or "
             "rated major or critical; developer-platform incidents (Workers, Durable Objects and the like) are "
             "left out."},
    {"id": "gworkspace", "source_id": "gworkspace-status", "provider": "google", "label": "Google Workspace",
     "publisher": "Google Workspace", "title": "Google Workspace Status Dashboard incidents",
     "url": "https://www.google.com/appsstatus/dashboard/incidents.json",
     "page": "https://www.google.com/appsstatus/dashboard/", "format": "gworkspace_json", "grade": "B2"},
    {"id": "apple", "source_id": "apple-status", "provider": "apple", "label": "Apple", "publisher": "Apple",
     "title": "Apple System Status (consumer services)",
     "url": "https://www.apple.com/support/systemstatus/data/system_status_en_US.js",
     "page": "https://www.apple.com/support/systemstatus/", "format": "apple_json", "grade": "B2",
     "licence": "No licence for the status data; Apple's website Terms of Use bar republishing site content without "
                "consent, so only the affected services, status type and times are kept, with a link; Apple's "
                "wording is not quoted",
     "note": "Consumer services (iCloud, App Store, Apple Music and others), not Apple's developer services. Only "
             "the affected services, type and times are shown; Apple's own wording is on its page."},
    {"id": "akamai", "source_id": "akamai-status", "provider": "akamai", "label": "Akamai", "publisher": "Akamai",
     "title": "Akamai Status incident history RSS (Atlassian Statuspage)",
     "url": "https://www.akamaistatus.com/history.rss", "page": "https://www.akamaistatus.com/",
     "format": "akamai_rss", "grade": "B2",
     "note": "Read from the incident history RSS (the site's robots.txt disallows its API). Customer Security "
             "Advisories are left out, and an incident older than the feed's 25 newest items would be missed."},
    {"id": "slack", "source_id": "slack-status", "provider": "salesforce", "label": "Slack",
     "publisher": "Slack (Salesforce)", "title": "Slack Status API, current incidents",
     "url": "https://slack-status.com/api/v2.0.0/current", "page": "https://slack-status.com/",
     "format": "slack_json", "grade": "B2"},
    {"id": "zoom", "source_id": "zoom-status", "provider": "zoom", "label": "Zoom", "publisher": "Zoom",
     "title": "Zoom status incident history RSS (Atlassian Statuspage)",
     "url": "https://www.zoomstatus.com/history.rss", "page": "https://www.zoomstatus.com/",
     "format": "statuspage_rss", "grade": "B2",
     "note": "Read from the incident history RSS (the site's robots.txt disallows its API). Maintenance notices fill "
             "most of its 25 items, so an incident open longer than about a week would be missed."},
]

FEED_BY_ID = {f["id"]: f for f in FEEDS}
# One name per provider for BO-IND-8, whichever of its feeds or networks reported (Azure or Microsoft 365; Google
# Cloud or Google Workspace). Slack is Salesforce's.
PROVIDER_LABELS = {"amazon": "Amazon", "google": "Google", "microsoft": "Microsoft", "starlink": "Starlink",
                   "cloudflare": "Cloudflare", "apple": "Apple", "akamai": "Akamai", "salesforce": "Salesforce",
                   "zoom": "Zoom"}

CONCURRENT_HOURS = 2     # BO-IND-8: onsets at different providers within 2 h of each other ...
RECENT_HOURS = 24        # ... both inside the last 24 h
# An open incident its provider hasn't updated in QUIET_DAYS is long-running: still listed, never counted as current
# (14 Nov 2026: AWS me-south-1 and me-central-1, open since 1-2 Mar 2026 and last updated 15 Sep, and Akamai's
# Indonesia-Singapore edge issue, last updated 29 Oct, made up three of the five "open incidents").
QUIET_DAYS = 14

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


GCP_LINK_BASE = "https://status.cloud.google.com/"
WORKSPACE_LINK_BASE = "https://www.google.com/appsstatus/dashboard/"


def parse_gcp_json(body: bytes, link_base: str = GCP_LINK_BASE) -> list[dict]:
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
            "link": link_base + uri.lstrip("/"),
        })
    return out


def parse_gworkspace_json(body: bytes) -> list[dict]:
    """Open Google Workspace incidents: the same shape and rule as Google Cloud's, linked on Workspace's dashboard."""
    return parse_gcp_json(body, link_base=WORKSPACE_LINK_BASE)


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
    lines of the message, each cut at its first colon. A post with no Status is a changed shape and is refused.
    Open is decided by Status alone: the 00:08 UTC 13 Nov 2026 update of an open incident had an empty Title, so
    the title falls back to Microsoft's own "User impact:" sentence. Microsoft gives each update a new Id."""
    data = json.loads(body)
    posts = data if isinstance(data, list) else [data]
    if not all(isinstance(p, dict) and "Status" in p for p in posts):
        raise ValueError("status post has no Status field")
    out = []
    for p in posts:
        status = str(p.get("Status") or "").strip()
        title = str(p.get("Title") or "").strip()
        if status.lower() in MSCLOUD_CLOSED:
            continue
        lines = _html_lines(str(p.get("Message") or ""))
        if not title:
            impact = next((ln.split(":", 1)[1].strip() for ln in lines if ln.lower().startswith("user impact:")),
                          "")
            title = impact.rstrip(".") or "Microsoft 365 issue (the post has no title)"
        affected = []
        for ln in lines:
            if ln.startswith("- "):
                name = ln[2:].split(":", 1)[0].strip()[:80]
                if name and name not in affected:
                    affected.append(name)
        out.append({
            "feed": "m365", "incident_id": str(p.get("Id") or p.get("LastUpdatedTime") or "current"),
            "title": html.unescape(title)[:200], "status": status,
            "service": None, "affected": affected, "locations": [], "started_at": None,
            "updated_at": _utc(_parse_time(p.get("LastUpdatedTime") or p.get("AuthoredDateTime"))), "updates": None,
            "link": "https://status.cloud.microsoft/",
        })
    return out


# Cloudflare components that are its network rather than a product built on it (names as on cloudflarestatus.com,
# 13 Nov 2026). Data centres are listed by location, "City, Country - (IATA)".
CLOUDFLARE_CORE = {"Network", "CDN/Cache", "Authoritative DNS", "Recursive DNS", "DNS Root Servers", "WARP",
                   "Magic Transit", "China Network"}
CLOUDFLARE_DC_RE = re.compile(r" - \([A-Z]{3}\)$")
CLOUDFLARE_IMPACT = {"major", "critical"}


def cloudflare_core(inc: dict) -> bool:
    """Whether a Cloudflare incident is kept: rated major or critical, or on a core network component or a data
    centre (see the module docstring for why both)."""
    return (str(inc.get("severity") or "").lower() in CLOUDFLARE_IMPACT
            or any(p in CLOUDFLARE_CORE or CLOUDFLARE_DC_RE.search(p) for p in inc.get("products") or []))


def parse_cloudflare_json(body: bytes) -> list[dict]:
    """Open Cloudflare incidents (Statuspage v2 unresolved.json) that touch its network, or are major or critical."""
    return [i for i in parse_statuspage(body) if cloudflare_core(i)]


APPLE_NOT_OPEN = {"resolved", "completed", "upcoming"}
APPLE_TYPES = ["Outage", "Issue", "Performance"]   # worst first
APPLE_TIME_RE = re.compile(r"^(\d{2})/(\d{2})/(\d{4}) (\d{2}):(\d{2}) (PST|PDT)$")
US_PACIFIC = {"PST": -8, "PDT": -7}


def apple_time(s) -> datetime | None:
    """Apple's "MM/DD/YYYY HH:MM PST|PDT" as UTC. Any other zone or form is None, never guessed."""
    m = APPLE_TIME_RE.match(str(s or "").strip())
    if not m:
        return None
    mo, d, y, hh, mm, zone = m.groups()
    local = datetime(int(y), int(mo), int(d), int(hh), int(mm), tzinfo=timezone(timedelta(hours=US_PACIFIC[zone])))
    return local.astimezone(timezone.utc)


def _apple_start(e: dict) -> datetime | None:
    ms = e.get("epochStartDate")
    if isinstance(ms, (int, float)) and not isinstance(ms, bool) and ms > 0:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return apple_time(e.get("startDate"))


def parse_apple_json(body: bytes) -> list[dict]:
    """Open Apple incidents: events not resolved, completed or upcoming, Maintenance left out, grouped by messageId
    (one outage is posted on each service it hit). Apple's message text is never kept (its site terms)."""
    data = json.loads(body)
    svcs = data.get("services") if isinstance(data, dict) else None
    if not isinstance(svcs, list):
        raise ValueError("system status has no services list")
    groups: dict[str, dict] = {}
    for s in svcs:
        if not isinstance(s, dict):
            continue
        for e in s.get("events") or []:
            if not isinstance(e, dict) or "eventStatus" not in e:
                raise ValueError("system status event has no eventStatus")
            if str(e.get("statusType") or "").lower() == "maintenance":
                continue
            if str(e.get("eventStatus") or "").lower() in APPLE_NOT_OPEN:
                continue
            key = str(e.get("messageId") or f"{e.get('startDate')}|{e.get('statusType')}")
            g = groups.setdefault(key, {"events": [], "carried_by": []})
            g["events"].append(e)
            name = str(s.get("serviceName") or "").strip()
            if name and name not in g["carried_by"]:
                g["carried_by"].append(name)
    out = []
    for key, g in groups.items():
        ev = g["events"]
        services = []
        for e in ev:
            for n in e.get("affectedServices") or []:
                if isinstance(n, str) and n.strip() and n.strip() not in services:
                    services.append(n.strip())
        services += [n for n in g["carried_by"] if n not in services]
        kinds = {str(e.get("statusType") or "") for e in ev}
        kind = next((k for k in APPLE_TYPES if k in kinds), sorted(kinds)[0] if kinds else "") or "Issue"
        starts = [t for t in (_apple_start(e) for e in ev) if t]
        posted = [t for t in (apple_time(e.get("datePosted")) for e in ev) if t]
        named = ", ".join(services[:3]) + (f" and {len(services) - 3} more" if len(services) > 3 else "")
        out.append({
            "feed": "apple", "incident_id": key, "title": f"{kind} on {named}" if named else kind,
            "status": str(ev[0].get("eventStatus")), "severity": kind, "service": None, "affected": services,
            "locations": [], "started_at": _utc(min(starts)) if starts else None,
            "updated_at": _utc(max(posted)) if posted else None, "updates": None,
            "link": "https://www.apple.com/support/systemstatus/",
        })
    return out


# Statuspage history RSS (history.rss): each item is an incident or a maintenance, its updates newest first.
SP_UPDATE_RE = re.compile(
    r"<small>\s*(?P<mon>[A-Z][a-z]{2})\s*<var data-var=['\"]date['\"]>\s*(?P<day>\d{1,2})\s*</var>,\s*"
    r"(?:<var data-var=['\"]year['\"]>\s*(?P<year>\d{4})\s*</var>,?\s*)?"
    r"<var data-var=['\"]time['\"]>\s*(?P<hm>\d{1,2}:\d{2})\s*</var>\s*(?P<zone>[A-Z]{2,5})\s*</small>\s*"
    r"<br\s*/?>\s*<strong>\s*(?P<status>[^<]+?)\s*</strong>")
SP_MAINTENANCE = {"scheduled", "in progress", "verifying", "completed"}
SP_RSS_CLOSED = {"resolved", "postmortem", "completed"}
SP_ZONES = {"UTC": 0, "GMT": 0, "PST": -8, "PDT": -7}   # those seen on the pages read; others are left unread
MONTHS = {m: i for i, m in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov",
                                      "Dec"), 1)}


def _sp_update_time(m: re.Match, published: datetime | None) -> datetime | None:
    """One update's time. The year is printed only when it isn't the current one; otherwise it is the item's
    pubDate year (the newest update), one less if that would put the update after the pubDate."""
    off = SP_ZONES.get(m.group("zone"))
    mon = MONTHS.get(m.group("mon"))
    if off is None or mon is None:
        return None
    hh, mm = (int(x) for x in m.group("hm").split(":"))
    tz = timezone(timedelta(hours=off))
    try:
        if m.group("year"):
            return datetime(int(m.group("year")), mon, int(m.group("day")), hh, mm, tzinfo=tz)
        if published is None:
            return None
        t = datetime(published.year, mon, int(m.group("day")), hh, mm, tzinfo=tz)
        return t if t <= published + timedelta(days=1) else t.replace(year=published.year - 1)
    except ValueError:
        return None


def parse_statuspage_rss(body: bytes, skip_title_prefixes: tuple = ()) -> list[dict]:
    """Open incidents from a Statuspage history.rss: the newest update's status is not Resolved, Postmortem or
    Completed. Items with a maintenance status (Scheduled, In progress, Verifying, Completed) are maintenances and
    are left out, as are titles starting with any of skip_title_prefixes. An item whose updates can't be read is a
    changed shape and is refused."""
    out = []
    for it in _xml(body).iter("item"):
        title = html.unescape(_text(it, "title"))
        desc = _text(it, "description")
        ups = list(SP_UPDATE_RE.finditer(desc))
        if not ups:
            raise ValueError(f"history item without readable updates: {title[:60]!r}")
        statuses = [u.group("status").strip().lower() for u in ups]
        if any(st in SP_MAINTENANCE for st in statuses) or "this is a scheduled event" in desc.lower():
            continue
        if statuses[0] in SP_RSS_CLOSED:
            continue
        if any(title.lower().startswith(p) for p in skip_title_prefixes):
            continue
        published = _parse_time(_text(it, "pubDate"))
        times = [_sp_update_time(u, published) for u in ups]
        link = _text(it, "link") or _text(it, "guid")
        out.append({
            "feed": "statuspage_rss", "incident_id": link.rstrip("/").rsplit("/", 1)[-1] or title,
            "title": title[:200], "status": next((st for st in statuses if st != "update"), statuses[0]),
            "severity": None, "service": None, "products": [], "locations": [],
            "started_at": _utc(times[-1]), "updated_at": _utc(times[0] or published), "updates": len(ups),
            "link": link,
        })
    return out


AKAMAI_SKIP = ("customer security advisory",)


def parse_akamai_rss(body: bytes) -> list[dict]:
    """Open Akamai incidents from its history RSS, Customer Security Advisories left out."""
    return parse_statuspage_rss(body, skip_title_prefixes=AKAMAI_SKIP)


SLACK_NOT_OPEN = {"resolved", "scheduled", "completed", "cancelled"}


def parse_slack_json(body: bytes) -> list[dict]:
    """Open Slack incidents from api/v2.0.0/current: active_incidents whose status is not resolved or a maintenance
    status (Slack's API docs: "active" until resolved; "scheduled", "completed", "cancelled" for maintenance)."""
    data = json.loads(body)
    incs = data.get("active_incidents") if isinstance(data, dict) else None
    if not isinstance(incs, list):
        raise ValueError("Slack status has no active_incidents list")
    out = []
    for inc in incs:
        if not isinstance(inc, dict) or str(inc.get("status") or "").lower() in SLACK_NOT_OPEN:
            continue
        url = str(inc.get("url") or "")
        out.append({
            "feed": "slack", "incident_id": str(inc.get("id") or url or inc.get("title")),
            "title": html.unescape(str(inc.get("title") or "")).strip()[:200], "status": inc.get("status"),
            "severity": inc.get("type"), "service": None,
            "affected": [x for x in inc.get("services") or [] if isinstance(x, str)], "locations": [],
            "started_at": _utc(_parse_time(inc.get("date_created"))),
            "updated_at": _utc(_parse_time(inc.get("date_updated"))),
            "updates": len(inc.get("notes") or []),
            "link": url if url.startswith("https://") else "https://slack-status.com/",
        })
    return out


PARSERS = {"aws_rss": parse_aws_rss, "gcp_json": parse_gcp_json, "azure_rss": parse_azure_rss,
           "mscloud_json": parse_mscloud_json, "statuspage": parse_statuspage,
           "cloudflare_json": parse_cloudflare_json, "gworkspace_json": parse_gworkspace_json,
           "apple_json": parse_apple_json, "akamai_rss": parse_akamai_rss, "statuspage_rss": parse_statuspage_rss,
           "slack_json": parse_slack_json}


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


def mark_quiet(incidents: list[dict], generated_at: str) -> list[dict]:
    """Each incident with `quiet_days` (whole days since the provider last updated it, else since it began) and
    `long_running` (quiet_days >= QUIET_DAYS). A time the feed doesn't give is never guessed: quiet_days is None and
    the incident counts as current."""
    now = _parse_time(generated_at)
    out = []
    for inc in incidents:
        last = _parse_time(inc.get("updated_at") or inc.get("started_at"))
        days = max(0, int((now - last).total_seconds() // 86400)) if now and last else None
        out.append({**inc, "quiet_days": days, "long_running": days is not None and days >= QUIET_DAYS})
    return out


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
