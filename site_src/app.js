// Blackout page: reads data/snapshot.json, data/feed.geojson (tic-feed/2) and data/method.json, and draws ten tabs;
// data/trends.json is read when the Trends tab is first shown.
// Rules kept here: feed text is always inserted as text (no HTML strings); no inline styles (CSP); measurements are
// never called judgments, and provider and Cloudflare items are labelled as their own reports; times go through
// tic-time.js. The map (MapLibre, map.js) loads only when the Overview is first shown.

const T = window.TicTime;
const $ = (id) => document.getElementById(id);

const TABS = ["overview", "outages", "censorship", "power", "routing", "cables", "providers", "satcom", "trends", "method"];
const TAB_NAME = { overview: "Overview", outages: "Outages", censorship: "Censorship", power: "Power", routing: "Routing",
  cables: "Cables", providers: "Providers", satcom: "Satellite", trends: "Trends", method: "Method" };
// Named places inside a tab that old links and the feed point at.
const SECTIONS = { radar: ["outages", "radar-card"], netblocks: ["outages", "netblocks-card"] };
const OONI_RATE = 0.15;
const OONI_MIN = 50;
const NEXT_RUN = "Blackout collects about every 4 hours";
// An open provider incident with no update in this many days is long-running: listed, not counted (status_feeds.QUIET_DAYS).
const QUIET_DAYS = 14;

// A duration a reader takes in at a glance: "45 min", "1 h 15 min", "56 h", "3 d 4 h" (ioda.duration_text).
function durText(minutes) {
  const m = Math.round(Number(minutes));
  if (!Number.isFinite(m)) return "an unknown time";
  if (m < 60) return `${m} min`;
  if (m < 600) return m % 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m / 60} h`;
  const hr = Math.round(m / 60);
  if (hr < 72) return `${hr} h`;
  return hr % 24 ? `${Math.floor(hr / 24)} d ${hr % 24} h` : `${hr / 24} d`;
}

// ---------------------------------------------------------------- DOM helpers (text only, never HTML)
function h(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") e.className = v;
    else if (k === "text") e.textContent = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false || kid === "") continue;
    e.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return e;
}
const timeEl = (iso, kind = "datetime") => (iso ? h("time", { datetime: iso, "data-tic": kind }, iso) : h("span", { text: "not given" }));
const badge = (text, kind = "") => h("span", { class: "badge" + (kind ? " b-" + kind : "") }, text);
const ext = (url, text) => (/^https:\/\//.test(url || "") ? h("a", { href: url, rel: "noopener" }, text) : null);
const pctDrop = (v) => (v == null ? "not measured" : v > 0 ? "-" + v + "%" : "no drop");
const pct1 = (r) => (r * 100).toFixed(1) + "%";
const fmt = (n) => (n == null ? "not given" : Number(n).toLocaleString("en-US"));
const signedPct = (x) => (x == null ? "not measured" : (x > 0 ? "+" : x < 0 ? "\u2212" : "") + Math.abs(Math.round(x * 100)) + "%");
const plural = (n, one, many) => `${fmt(n)} ${n === 1 ? one : many || one + "s"}`;
const human = (s) => (s ? String(s).replace(/_/g, " ").toLowerCase() : "");
const keyOf = (s) => String(s || "").trim().toLowerCase();
function emptyNote(...kids) { return h("p", { class: "empty" }, kids); }
function joinNodes(parts, sep = " · ") {
  const out = [];
  parts.filter((p) => p != null && p !== false && p !== "").forEach((p, i) => { if (i) out.push(sep); out.push(p); });
  return out;
}

// ---------------------------------------------------------------- data
const S = { snap: null, feed: null, method: null, shapes: null, feats: [], map: null, mapLoading: null, mapFailed: false,
  layers: { outages: true, ooni: true, radar: true, power: true, routing: true, corridors: true, tips: true }, selected: null };

async function getJSON(url, optional) {
  try {
    const r = await fetch(url, { cache: "no-cache" });
    if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
    return await r.json();
  } catch (err) {
    if (optional) return null;
    throw err;
  }
}

const published = (o) => Boolean(o.indicator_id) && o.lat != null && o.lon != null;
const ooniHit = (c) => c.anomaly_rate >= OONI_RATE && c.measurement_count >= OONI_MIN;
function slug(s) {  // feed.py's incident slug: non-alphanumerics to "-", ends trimmed, 80 characters
  return String(s).toLowerCase().replace(/[^\p{L}\p{N}]/gu, "-").replace(/^-+|-+$/g, "").slice(0, 80);
}
function feat(prefix, exact) {
  const f = S.feats.find((x) => (exact ? x.properties.id === prefix : x.properties.id.startsWith(prefix)));
  return f ? f.properties : null;
}

function model() {
  const snap = S.snap;
  const r = snap.radar || {};
  return {
    outages: (snap.outages || []).filter(published).sort((a, b) => b.drop_pct - a.drop_pct),
    ooni: (snap.censorship || []).filter(ooniHit).sort((a, b) => b.anomaly_rate - a.anomaly_rate),
    ooniBelow: (snap.censorship || []).filter((c) => !ooniHit(c)).sort((a, b) => b.anomaly_rate - a.anomaly_rate),
    annotations: r.annotations || [],
    anomalies: r.anomalies || [],
    alerts: snap.corridor_alerts || [],
    corridors: snap.corridors || [],
    tips: snap.tips || [],
    incidents: (snap.provider_incidents || []).filter((i) => !i.long_running),
    quietIncidents: (snap.provider_incidents || []).filter((i) => i.long_running),
    feeds: snap.provider_feeds || [],
    watch: snap.asn_watch || [],
    watchlist: snap.asn_watchlist || [],
    concurrency: snap.provider_concurrency || null,
    znpp: snap.znpp || null,
    eia: snap.eia || null,
    odin: snap.odin || null,
    night: snap.nightlights || null,
    radarPower: (r.annotations || []).filter((a) => a.cause === "POWER_OUTAGE"),
    bgp: snap.bgp || null,
    stop: snap.stop || null,
    testlists: snap.testlists || null,
    faa: snap.faa || null,
    faaStops: ((snap.faa || {}).events || []),
    bluesky: snap.bluesky || null,
    gdelt: snap.gdelt || null,
    publicSurges: publicSurges(snap),
  };
}
// Feed items on the Power and Routing tabs, with the key their link carries (#power/<key>, #routing/<key>).
function laneFeats(tab) {
  return S.feats.filter((f) => ((f.properties.visual || {}).url || "").includes(`#${tab}/`))
    .map((f) => ({ f, p: f.properties, key: keyOf(f.properties.visual.url.split(`#${tab}/`)[1]) }));
}

// ---------------------------------------------------------------- doctrine detail, opened on each item
const REPORTED = /^blackout:(provider-status|radar-|bgp-|znpp-power|us-power|faa:|public:|netblocks:)/;
function methodText(list, id) {
  const x = ((S.method && S.method[list]) || []).find((i) => i.id === id);
  return x ? x.text || x.title : "";
}
function doctrine(p) {
  if (!p) return null;
  const judged = p.kind === "assessment";
  const reported = REPORTED.test(p.id);
  const head = judged
    ? `Judgment: ${p.likelihood_term || "likelihood not given"}` +
      (p.probability != null ? ` (${Math.round(p.probability * 100)}%)` : "") +
      (p.confidence ? `, ${p.confidence} confidence` : "")
    : reported ? "Reported by the source; Blackout does not assess its cause" : "Measurement, no probability";
  const dl = h("dl", { class: "dl" });
  const add = (k, ...v) => { const vals = v.flat().filter((x) => x != null && x !== ""); if (vals.length) dl.append(h("dt", {}, k), h("dd", {}, vals)); };
  add("Kind", `${p.kind}, tier ${p.tier}` + (p.status_class ? `, ${p.status_class}` : ""));
  if (judged) {
    add("Likelihood", p.likelihood_term ? `${p.likelihood_term}` + (p.probability != null ? ` (${Math.round(p.probability * 100)}%)` : "") : null);
    add("Confidence", p.confidence ? `${p.confidence}` + (p.confidence_basis ? `: ${p.confidence_basis}` : "") : null);
  } else if (p.confidence_basis) {
    add("Basis", p.confidence_basis);
  }
  add("Warning problem", p.warning_problem_id ? `${p.warning_problem_id}` + (methodText("warning_problems", p.warning_problem_id) ? `: ${methodText("warning_problems", p.warning_problem_id)}` : "") : null);
  add("Hypothesis", p.hypothesis_id ? `${p.hypothesis_id}` + (p.direction ? ` (evidence ${p.direction === "neutral" ? "is neutral" : p.direction + " it"})` : "") : null);
  add("Indicator", p.indicator_id ? `${p.indicator_id}` + (methodText("indicators", p.indicator_id) ? `: ${methodText("indicators", p.indicator_id)}` : "") : null);
  add("National Critical Functions", (p.ncf || []).map(human).join(", "));
  add("Requirements", (p.requirement_ids || []).join(", "));
  if ((p.alternatives || []).length) {
    add("Competing hypotheses", h("ul", { class: "alts" }, p.alternatives.map((a) =>
      h("li", {}, h("strong", {}, a.hypothesis), a.tell_apart ? [" Tell apart: ", a.tell_apart] : null))));
  }
  if ((p.sources || []).length) {
    add("Sources", h("ul", { class: "alts" }, p.sources.map((s) => {
      const meta = joinNodes([s.grade ? `grade ${s.grade}` : null, s.licence || null, ext(s.url, "link")], " · ");
      return h("li", {}, h("strong", {}, s.publisher || s.id), s.title ? `: ${s.title}` : "", meta.length ? [" (", meta, ")"] : null);
    })));
  }
  add("Item", h("code", {}, p.id));
  add("Data as of", timeEl(p.data_as_of));
  return h("details", { class: "doctrine" }, h("summary", {}, head), dl);
}
function kindBadge(p, who) {
  if (!p) return null;
  if (p.kind === "assessment") return badge("Judgment", "warn");
  if (REPORTED.test(p.id)) return badge(`Reported by ${who}`, "rep");
  return badge("Measurement", "meas");
}

// ---------------------------------------------------------------- rows (one builder per kind; the map panel reuses them)
function row({ keys, tab, title, badges, lines, p, cls, label }) {
  return h("article", { class: "row" + (cls ? " " + cls : ""), "data-keys": keys.filter(Boolean).map(keyOf).join(" "), "data-tab": tab,
    "data-label": label || null },
    h("div", { class: "row-title" }, h("h3", { class: "row-h" }, title), h("span", { class: "badges" }, badges)),
    lines.filter(Boolean).map((l) => (l.nodeType ? l : h("p", { class: "line" }, l))),
    doctrine(p));
}
const muted = (...kids) => h("p", { class: "line muted" }, kids);

// Context only: Access Now's shutdown history and Citizen Lab's test lists never change a judgment.
// About how many people a country's outage could reach (not how many it did): ITU's share online and the
// population that year, via the World Bank (itu.py). Context only.
function approxPeople(n) {
  if (n >= 1e7) return `${Math.round(n / 1e6)} million`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)} million`;
  const k = 10 ** Math.max(0, Math.floor(Math.log10(Math.max(n, 1))) - 1);
  return fmt(Math.round(n / k) * k);
}

function useLine(cc) {
  const u = S.snap && S.snap.internet_use;
  if (!u || !cc) return null;
  const c = (u.countries || {})[cc];
  if (!c) return muted("Internet use: ITU has no figure for this country in the World Bank's tables. Context only.");
  const [pct, year, pop] = c;
  return muted(`Exposure, an upper bound: ${pct}% of people used the internet in ${year} (ITU)` +
    (pop ? `, about ${approxPeople((pct / 100) * pop)} of ${approxPeople(pop)}` : "") +
    ". How many people an outage here could reach, never how many it did.");
}

// The impact measure: IODA's active-probing level and the level NetBlocks states, as percent of normal (feed.py
// percent_of_normal). Exposure (useLine) is only the ceiling it applies to.
function ponLine(p) {
  const v = p && p.percent_of_normal;
  if (!v || (v.ioda_active_probing_pct == null && v.netblocks_level_pct == null)) return null;
  return muted("Percent of normal: ", ...joinNodes([
    v.ioda_active_probing_pct != null ? `IODA active probing ${v.ioda_active_probing_pct}% of its usual level` : null,
    v.netblocks_level_pct != null ? h("span", {}, `NetBlocks states ${v.netblocks_level_pct}% `, ext(v.netblocks_url, "(post)")) : null]));
}

function stopLine(cc) {
  const st = S.snap && S.snap.stop;
  if (!st || !cc) return null;
  const c = (st.countries || []).find((x) => x.country === cc);
  if (!c) return muted(`Shutdown history: Access Now's STOP sheet lists no shutdown here in ${st.year}. Context only.`);
  return muted(`Shutdown history: Access Now's STOP sheet lists ${plural(c.shutdowns, "shutdown")} here in ${st.year}` +
    ((c.causes || []).length ? ` (stated causes: ${c.causes.join(", ").toLowerCase()})` : "") +
    `, the latest from ${c.latest_start}. Context only, not evidence about this item.`);
}
function catName(code) {
  const t = S.snap && S.snap.testlists;
  return ((t && t.legend && t.legend[code]) || {}).name || code;
}
function testListLine(cc) {
  const t = S.snap && S.snap.testlists;
  if (!t || !cc || !(cc in (t.countries || {}))) return null;
  const l = t.countries[cc];
  const glob = t.global ? ` and the global list of ${fmt(t.global.urls)}` : "";
  if (!l) return muted(`What OONI tests: Citizen Lab keeps no list for this country, so probes here test the global list${t.global ? ` (${fmt(t.global.urls)} URLs)` : ""}.`);
  const top = Object.entries(l.categories || {}).slice(0, 3).map(([k, n]) => `${catName(k).toLowerCase()} ${n}`).join(", ");
  return muted(`What OONI tests: Citizen Lab's list for this country holds ${fmt(l.urls)} URLs` + (top ? ` (most in ${top})` : "") +
    glob + (l.latest_added ? `; newest added ${l.latest_added}` : "") + ".");
}

function outageRow(o) {
  const p = feat(`blackout:outage:${o.country.toLowerCase()}:`);
  const single = o.indicator_id === "BO-IND-4";
  const judged = p && p.kind === "assessment";
  const blocking = p && p.direction === "supports" && p.hypothesis_id === "BO-H-1A";  // OONI blocking rose in its hours
  const related = (o.related_tips || []).map((t) => `${(t.program || "suite").toUpperCase()}: ${t.title || t.item_id}`).join("; ");
  return row({ keys: [o.country, o.id], tab: "outages", cls: judged ? "warn" : "", label: judged ? "Likely shutdown (IODA and OONI)" : blocking ? "Outage, with OONI blocking rising in its hours" : single ? "Single-signal dip measured by IODA" : "Outage measured by IODA",
    title: `${o.country_name} (${o.country})`,
    badges: [kindBadge(p), badge("-" + o.drop_pct + "%", single ? "" : "danger"), badge(o.indicator_id + (single ? ", single-source dip" : ""), "doc"),
      o.corroborated ? badge("BGP and probing agree", "tip") : null],
    lines: [
      h("p", { class: "line" }, o.asn ? [h("code", {}, o.asn), ` ${o.asn_name || ""}: `] : null, o.summary),
      muted("From ", timeEl(o.start_time), ` for ${durText(o.duration_minutes)} · BGP ${pctDrop(o.bgp_drop_pct)} · probing ${pctDrop(o.probing_drop_pct)} · darknet ${pctDrop(o.darknet_drop_pct)}`,
        o.baseline_zscore != null ? ` · z ${o.baseline_zscore} against the 7 days before` : "",
        o.ioda_score != null ? ` · IODA score ${Math.round(o.ioda_score)} (a magnitude, not a percentage)` : ""),
      related ? muted("Context (does not change the assessment): " + related) : null,
      ponLine(p),
      stopLine(o.country),
      useLine(o.country),
    ], p });
}

// Where Cloudflare places an outage. A PLATFORM outage is a service's (Microsoft 365, 12 Nov 2026) and may name no
// place at all (feed.py PLATFORM_WHERE says the same).
function radarWhere(a) {
  const where = (a.regions && a.regions[0]) || (a.countries || []).join(", ");
  // Cloudflare named only the network: the country is where Radar registers it (radar.network_countries).
  if (where && a.country_basis === "network") return `${where} (network's country)`;
  return where || (a.type === "PLATFORM" ? "Platform outage, no place named" : "Location not given");
}

function radarAnnotationRow(a) {
  const p = feat(`blackout:radar-outage:${a.radar_id}`, true);
  const span = a.end ? ["From ", timeEl(a.start), " to ", timeEl(a.end)] : ["Since ", timeEl(a.start), ", ongoing"];
  return row({ keys: ["radar", ...(a.countries || [])], tab: "outages", label: `Cloudflare Radar outage: ${human(a.cause) || "cause not stated"}`,
    title: `${radarWhere(a)}: ${human(a.cause) || "cause not stated"}`,
    badges: [kindBadge(p, "Cloudflare"), a.type ? badge(human(a.type)) : null, badge("BO-IND-9", "doc")],
    lines: [a.description ? h("p", { class: "line" }, a.description) : null,
      muted(joinNodes([span, (a.asns || []).length ? a.asns.map((n) => `AS${n}` + ((a.asn_names || {})[n] ? ` ${a.asn_names[n]}` : "")).join(", ") : null, ext(a.link, "Cloudflare's post")])),
      a.country_basis === "network" ? muted("Cloudflare names no location, only the network; the country is the one Radar's network details give for it, not a measured place.") : null,
      muted("Cause as Cloudflare states it; Blackout does not assess it.")],
    p });
}

function radarAnomalyRow(a) {
  const p = feat(`blackout:radar-anomaly:${a.uuid}`, true);
  const span = a.end ? ["From ", timeEl(a.start), " to ", timeEl(a.end)] : ["Since ", timeEl(a.start), ", ongoing"];
  return row({ keys: ["radar", a.country, a.asn ? "AS" + a.asn : ""], tab: "outages",
    label: `Cloudflare Radar traffic anomaly${a.asn ? `, AS${a.asn}` + (a.asn_name ? ` (${a.asn_name})` : "") : ""}`,
    title: `${countryName(a.country) || "Location not given"}${a.asn ? `, AS${a.asn}` + (a.asn_name ? ` (${a.asn_name})` : "") : ""}: traffic anomaly`,
    badges: [kindBadge(p, "Cloudflare"), badge(`verified (${a.status})`), badge("BO-IND-10", "doc")],
    lines: [muted(span, (a.seen_in || []).length ? ` · seen in Radar's ${a.seen_in.join(", ")} data` : ""),
      muted("Radar gives no cause for an anomaly; Blackout does not assess one.")],
    p });
}

// NetBlocks' own reports (BO-IND-20): its words quoted with the link to the post, never assessed here.
function netblocksRow(x) {
  const p = feat(`blackout:netblocks:${x.id}`, true);
  const where = (x.regions || []).join(", ") || (x.countries || []).map(countryName).join(", ");
  const what = (x.platforms || []).join(", ") || (x.asns || []).map((a) => "AS" + a).join(", ");
  return row({ keys: ["netblocks", `netblocks-${x.id}`, ...(x.countries || [])], tab: "outages",
    label: `NetBlocks report${x.type ? ": " + x.type : ""}`,
    title: `${where || what || "Place not named"}${where && what ? " · " + what : ""}`,
    badges: [kindBadge(p, "NetBlocks"), x.level_pct != null ? badge(`${x.level_pct}${x.level_basis.slice(1)}`, "danger") : null,
      (x.causes || []).length ? badge(x.causes.join(", ")) : null, badge("BO-IND-20", "doc")],
    lines: [h("p", { class: "line" }, "\u201c", x.excerpt, "\u201d"),
      muted(joinNodes([["Posted ", timeEl(x.posted_at)], ext(x.url, "NetBlocks' full post")])),
      muted("NetBlocks' report, quoted; Blackout does not assess it. Copyright NetBlocks.")],
    p });
}

function ooniRow(c) {
  const p = feat(`blackout:ooni:${c.country.toLowerCase()}:`);
  return row({ keys: [c.country, c.id], tab: "censorship", title: `${c.country_name} (${c.country})`, label: "Censorship measured by OONI",
    badges: [kindBadge(p), badge(pct1(c.anomaly_rate) + " anomalous or blocked", "ooni"), badge("BO-IND-3", "doc")],
    lines: [c.summary,
      muted(`Confirmed blocking ${pct1(c.confirmed_rate)} · ${c.measurement_count.toLocaleString("en-US")} measurements` +
        (c.ok_count != null ? ` · ${c.ok_count.toLocaleString("en-US")} OK` : " · OK count not reported") +
        (c.test_name ? ` · ${c.test_name}` : "")),
      testListLine(c.country), stopLine(c.country), useLine(c.country)],
    p });
}

function corridorAlertRow(c) {
  const p = feat(`blackout:corridor:${c.corridor_id}:`);
  return row({ keys: [c.corridor_id, c.id, ...(c.countries || [])], tab: "cables", cls: "warn", title: c.name,
    badges: [kindBadge(p), badge(`mean -${c.mean_drop_pct}%`, "warn"), (c.indicator_ids || ["BO-IND-2"]).map((id) => badge(id, "doc")),
      (c.tips || []).map((t) => badge(`Tip: ${String(t.program).toUpperCase()} (${t.distance_km} km)`, "tip"))],
    lines: [c.summary,
      muted("Countries: " + (c.countries || []).join(", ") + " · since ", timeEl(c.start_time)),
      muted("Cables: " + (c.cables || []).join(", ") + " · Landing stations: " + (c.landing_stations || []).join("; "))],
    p });
}

function corridorCard(c, alert) {
  return h("article", { class: "reg" + (alert ? " warn" : ""), "data-keys": keyOf(c.id), "data-tab": "cables" },
    h("h3", { class: "row-h" }, c.name),
    h("p", { class: "line" }, alert ? badge(`Alert: mean -${alert.mean_drop_pct}%`, "warn") : badge("No alert this run")),
    h("p", { class: "line muted" }, "Cables: " + (c.cables || []).join(", ")),
    h("p", { class: "line muted" }, "Landing stations: " + (c.landing_stations || []).join("; ")),
    h("p", { class: "line muted" }, "Countries served: " + (c.countries || []).join(", ")));
}

function tipRow(t) {
  return row({ keys: [t.item_id, t.program, ...(t.countries || [])], tab: "cables",
    title: `${(t.program || "suite").replace(/^./, (m) => m.toUpperCase())}: ${t.title}`,
    badges: [badge(t.interest_id || "BO-INT", "tip"), badge(t.indicator_id || "BO-IND-5", "doc")],
    lines: [muted(joinNodes([`Countries: ${(t.countries || []).join(", ") || "none given"}`, `tier ${t.tier || "not given"}`,
      t.lat != null && t.lon != null ? `at ${t.lat}, ${t.lon}` : "no location, so it cannot meet BO-IND-5",
      t.published_at ? ["published ", timeEl(t.published_at)] : null, ext(t.link, "source item")]))],
    p: null });
}

function incidentRow(i) {
  const p = feat(`blackout:provider-status:${i.feed}:${slug(i.incident_id)}`, true);
  const where = (i.locations && i.locations.length) ? i.locations : (i.affected || []);
  const loc = (i.locations || []).length ? ` (${i.locations.slice(0, 3).join(", ")}${i.locations.length > 3 ? ` and ${i.locations.length - 3} more` : ""})` : "";
  return row({ keys: ["provider-" + i.feed, i.provider, "providers"], tab: "providers",
    title: `${i.provider_label}: ${i.title}${loc}`,
    badges: [kindBadge(p, "the provider"), i.status ? badge(i.status, "warn") : null, i.severity ? badge(i.severity) : null, badge("BO-IND-7", "doc")],
    lines: [muted(joinNodes([i.started_at ? ["Since ", timeEl(i.started_at)] : "Start not given",
      i.updated_at ? ["last update ", timeEl(i.updated_at)] : null, i.updates ? `${i.updates} update(s)` : null, i.service || null,
      ext(i.link, "provider's page")])),
      where.length ? muted("Affected, as the provider lists them: " + where.slice(0, 12).join(", ") + (where.length > 12 ? ` and ${where.length - 12} more` : "")) : null,
      i.long_running ? muted(`Still open on ${i.provider_label}'s page, with no update in ${i.quiet_days} days: listed as long-running, not counted as current.`) : null],
    p });
}

// FAA ground stops (BO-IND-17): what the FAA's Command Center stopped, as it says, with the advisories linked.
const FAA_KIND = { national: "Every US airport", airline: "Whole airline", carrier: "Carrier request, several airports", equipment: "Equipment or communications" };
function faaState(e) { return e.in_effect ? "in effect" : e.cancelled_at ? "cancelled" : "ended"; }
function faaTitle(e) {
  if (e.kind === "national") return "Ground stop at every US airport";
  if (e.kind === "airline") return `Ground stop for all ${e.airline || e.key} (${e.key}) flights`;
  if (e.kind === "carrier") return `Ground stops at ${e.airports.length} airports at a carrier's request (${e.airports.join(", ")})`;
  return `Ground stop at ${e.key}: ${e.condition || "equipment outage"}`;
}
function faaRow(e) {
  const p = feat(`blackout:faa:${e.id}`, true);
  const hot = e.kind !== "equipment" || (e.cluster || []).length >= 2;
  return row({ keys: ["faa-" + slug(e.id), "faa", "providers"], tab: "providers", cls: hot && e.in_effect ? "warn" : "",
    label: "FAA ground stop", title: faaTitle(e),
    badges: [kindBadge(p, "the FAA"), badge(faaState(e), e.in_effect ? "warn" : ""), badge(FAA_KIND[e.kind] || e.kind), badge("BO-IND-17", "doc")],
    lines: [muted(joinNodes([["From ", timeEl(e.started_at)],
      e.cancelled_at ? ["cancelled ", timeEl(e.cancelled_at)] : e.in_effect ? ["effective to ", timeEl(e.ends_at), " unless extended or cancelled"] : ["to ", timeEl(e.ends_at), " (last effective time given)"],
      ext(e.link, "FAA advisory")])),
      e.condition ? muted(`Condition the FAA names: ${e.condition}.`) : null,
      e.comments ? muted(`FAA comments: ${e.comments}`) : null,
      e.kind === "carrier" ? muted(`Airlines named in the advisories: ${(e.carriers || []).join(", ") || "none"}. A carrier asks for a stop; the FAA does not say why.`) : null,
      e.kind === "equipment" && (e.cluster || []).length >= 2 ? muted(`Equipment stops at ${e.cluster.join(", ")} began within 2 h of each other.`) : null,
      muted(`Advisories ${e.advisories.map((a) => a.advn).join(", ")}. Reported by the FAA; Blackout does not assess why the system failed.`)],
    p });
}

// Outages the public (Bluesky, BO-IND-18) and the press (GDELT, BO-IND-19) report: one row per watched name that
// surged in either lane, both lanes' counts side by side. Counts and links only; no post or headline text.
function publicKey(lane, r, ref) { return `${lane}-${r.key}-${String(ref || "").slice(0, 10)}`; }
function publicSurges(snap) {
  const b = snap.bluesky || {}, g = snap.gdelt || {};
  const by = new Map();
  for (const r of b.surges || []) by.set(r.key, { key: r.key, label: r.label, b: r, bKey: publicKey("bluesky", r, r.first_hour || b.window_end) });
  for (const r of g.surges || []) {
    const x = by.get(r.key) || { key: r.key, label: r.label };
    by.set(r.key, Object.assign(x, { g: r, gKey: publicKey("gdelt", r, r.first_file || g.window_end) }));
  }
  return [...by.values()].sort((x, y) => ((y.b ? y.b.ratio : 0) + (y.g ? y.g.ratio : 0)) - ((x.b ? x.b.ratio : 0) + (x.g ? x.g.ratio : 0)));
}
function publicRow(x, m) {
  const pb = x.b ? feat(`blackout:public:${x.bKey}`, true) : null;
  const pg = x.g ? feat(`blackout:public:${x.gKey}`, true) : null;
  const who = x.b && x.g ? "the public and the press" : x.b ? "the public" : "the press";
  const lines = [];
  if (x.b) {
    lines.push(muted(joinNodes([`Bluesky: ${plural(x.b.matches, "public post")} naming “${x.b.name}” with “down” or “outage” in ${m.bluesky.window_h} h; usual ${fmt(x.b.usual)}`,
      x.b.first_hour ? [x.b.first_hour === m.bluesky.window_start ? "already over the line from " : "rose from ", timeEl(x.b.first_hour)] : null, x.b.peak_hour ? ["peaked in the hour from ", timeEl(x.b.peak_hour)] : null,
      ext(x.b.link, "Bluesky's own search")])));
  }
  if (x.g) {
    const doms = x.g.outlet_domains || [];
    lines.push(muted(joinNodes([`News: ${plural(x.g.headlines, "headline")} from ${plural(x.g.outlets, "outlet")} in ${m.gdelt.window_h} h (GDELT); usual ${fmt(x.g.usual)}`,
      x.g.first_file ? ["first in GDELT's file of ", timeEl(x.g.first_file)] : null])));
    lines.push(muted("Outlets: ", doms.slice(0, 10).join(", "), doms.length > 10 ? ` and ${doms.length - 10} more` : "", "."));
    const links = (x.g.links || []).slice(0, 5);
    if (links.length) lines.push(muted("Articles: ", ...joinNodes(links.map((l) => ext(l.url, l.domain)), ", "), " (headlines not reproduced)."));
  }
  lines.push(muted(`Reported by ${who}, not confirmed by the company. Blackout counts posts and headlines and does not assess a cause; a surge can also be news of an outage elsewhere, a joke, or the company's denial.`));
  if (pb && pg) lines.push(doctrine(pg));  // the news item's detail; row() adds the Bluesky item's after it
  return row({ keys: [x.bKey, x.gKey, "public", "providers", x.key], tab: "providers", cls: x.b && x.g ? "warn" : "",
    label: "Reported outage", title: `${x.label}: reported down`,
    badges: [kindBadge(pb || pg, who), x.b ? badge("BO-IND-18", "doc") : null, x.g ? badge("BO-IND-19", "doc") : null],
    lines, p: pb || pg });
}
function publicTable(m) {
  const b = new Map(((m.bluesky || {}).rows || []).map((r) => [r.key, r]));
  const g = new Map(((m.gdelt || {}).rows || []).map((r) => [r.key, r]));
  const keys = [...new Set([...b.keys(), ...g.keys()])];
  if (!keys.length) return null;
  const cell = (r, n) => (r ? `${fmt(r[n])} (usual ${fmt(r.usual)})` : "");
  const rows = keys.map((k) => {
    const rb = b.get(k), rg = g.get(k);
    return tr([`public-${k}`], "providers", [(rb || rg).label, rb ? cell(rb, "matches") : (m.bluesky ? "not read" : ""),
      rg ? cell(rg, "headlines") : (m.gdelt ? "0" : "")]);
  });
  return h("details", { class: "below" }, h("summary", {}, `Every watched name this run (${keys.length})`),
    table("Bluesky posts and news headlines per watched name", ["Name", `Bluesky posts (${(m.bluesky || {}).window_h || 6} h)`, `News headlines (${(m.gdelt || {}).window_h || 6} h)`], rows));
}

function concurrencyRow(c) {
  const p = feat("blackout:provider-concurrent:");
  return row({ keys: ["providers", ...(c.providers || [])], tab: "providers", cls: "warn",
    title: `Outages at ${c.providers.length} providers began within 2 h: ${c.provider_labels.join(", ")}`,
    badges: [kindBadge(p), badge("BO-IND-8", "doc")],
    lines: [h("p", { class: "line" }, joinNodes(c.onsets.map((o) => [`${o.label} from `, timeEl(o.onset)]), "; "), "."),
      muted("Onsets this close do not by themselves show a shared cause or a cascade.", c.caution ? " " + c.caution : "")],
    p });
}

function watchRow(w) {
  const r = (S.snap.asn_watch || []).find((x) => x.asn === w.asn);
  const live = S.snap.mode === "live";
  if (!r) {
    return row({ keys: [w.asn, w.provider], tab: "providers", title: `${w.label} (${w.asn})`,
      badges: [badge(live ? "not read this run" : "no reading (seed build)", live ? "danger" : "")],
      lines: [live ? muted("IODA could not be read for this network; see the problems in the snapshot JSON. Nothing is shown in its place.") : null], p: null });
  }
  const hit = r.indicator_id === "BO-IND-6";
  const p = hit ? feat(`blackout:provider-net:${r.asn.toLowerCase()}:`) : null;
  if (!hit) {
    return h("div", { class: "feed", "data-keys": keyOf(r.asn) + " " + keyOf(r.provider), "data-tab": "providers" },
      h("div", { class: "feed-h" }, h("strong", {}, `${r.label} (${r.asn})`), badge(pctDrop(r.drop_pct)),
        r.ioda_events ? badge(`${r.ioda_events} IODA event(s)`, "warn") : null),
      h("p", { class: "line muted" }, `BGP ${pctDrop(r.bgp_drop_pct)}` + (r.bgp_zscore != null ? ` (z ${r.bgp_zscore})` : "") +
        ` · probing ${pctDrop(r.probing_drop_pct)}` + (r.probing_zscore != null ? ` (z ${r.probing_zscore})` : "")),
      h("p", { class: "line muted" }, "Window to ", timeEl(r.window_end), ` (${r.window_basis})`));
  }
  return row({ keys: [r.asn, r.provider], tab: "providers", cls: hit ? "warn" : "", title: `${r.label} (${r.asn})`,
    badges: [hit ? kindBadge(p) : null, badge(pctDrop(r.drop_pct), hit ? "danger" : ""), hit ? badge("BO-IND-6", "doc") : null,
      r.ioda_events ? badge(`${r.ioda_events} IODA event(s)`, "warn") : null],
    lines: [muted(`${r.asn_name || ""} · BGP ${pctDrop(r.bgp_drop_pct)}` + (r.bgp_zscore != null ? ` (z ${r.bgp_zscore})` : "") +
        ` · probing ${pctDrop(r.probing_drop_pct)}` + (r.probing_zscore != null ? ` (z ${r.probing_zscore})` : "")),
      muted("Window ", timeEl(r.window_start), " to ", timeEl(r.window_end), ` (${r.window_basis})`)],
    p });
}

function feedRow(f) {
  const state = !f.read ? badge("not read this run", "danger") : f.open ? badge(`${f.open} open`, "warn") : badge("none listed");
  return h("div", { class: "feed", "data-keys": keyOf("provider-" + f.id) + " " + keyOf(f.provider), "data-tab": "providers" },
    h("div", { class: "feed-h" }, h("strong", {}, f.label), state, badge(`grade ${f.grade}`)),
    h("p", { class: "line muted" }, f.publisher, " · ", ext(f.page, "status page") || "no page"),
    f.note ? h("details", { class: "feed-note" }, h("summary", {}, "About this feed"), h("p", { class: "line muted" }, f.note)) : null);
}

// ---------------------------------------------------------------- Power and Routing rows
function table(caption, head, rows) {
  return h("div", { class: "tbl-wrap" }, h("table", { class: "tbl" }, h("caption", { class: "sr-only" }, caption),
    h("thead", {}, h("tr", {}, head.map((c) => h("th", { scope: "col" }, c)))), h("tbody", {}, rows)));
}
function tr(keys, tab, cells) {
  return h("tr", { "data-keys": keys.filter(Boolean).map(keyOf).join(" "), "data-tab": tab },
    cells.map((c, i) => (i === 0 ? h("th", { scope: "row" }, c) : h("td", {}, c))));
}
const day = (iso) => (iso ? h("time", { datetime: iso.length === 10 ? iso + "T12:00:00Z" : iso, "data-tic": "date", "data-tic-year": true }, iso) : "date not read");

const ZNPP_STATE = { none: "without off-site power", single: "on a single off-site power line",
  not_stated: "off-site power not stated in the updates read" };
function znppRow(z) {
  const p = feat("blackout:znpp-power:");
  const s = z.stated_in || (z.state !== "not_stated" ? z : null);
  const state = s ? s.state : "not_stated";
  const which = s || z;
  return row({ keys: ["znpp"], tab: "power", cls: state === "none" ? "warn" : "", label: "Zaporizhzhya NPP off-site power (IAEA)",
    title: `Zaporizhzhya Nuclear Power Plant: ${ZNPP_STATE[state]}`,
    badges: [kindBadge(p, "the IAEA"), badge(`IAEA Update ${which.number}`), badge("BO-IND-13", "doc")],
    lines: [
      s ? h("p", { class: "line" }, `The IAEA, Update ${s.number}: `, joinNodes((s.quotes || []).slice(0, 2).map((q) => h("q", {}, q)), " "))
        : h("p", { class: "line" }, `None of the updates read, the newest being Update ${z.number}, states the plant's current off-site power, so it is not known here; that is not read as calm.`),
      s && s !== z ? muted(`Update ${z.number} says nothing current about the plant's off-site power; Update ${s.number} is the newest that does.`) : null,
      (z.losses || []).length ? muted("Loss of power it reports: ", h("q", {}, z.losses[0])) : null,
      z.loop_count ? muted(`The IAEA counts ${z.loop_count} complete losses of off-site power since the war began.`) : null,
      muted(joinNodes([["Update dated ", day(which.date)], ext(which.link, "the IAEA's update"), ext(which.archived, "archived copy read")])),
      (z.newer_unread || []).length ? muted(`Update ${z.newest_number} is listed by the IAEA but its text isn't archived yet, so it isn't read here.`) : null,
      z.overdue ? muted(`No newer numbered update was found, and this one is ${z.age_days} days old; the IAEA has issued them about weekly, so one may be missing.`) : null,
      muted("Reported by the IAEA and quoted; Blackout does not assess it."),
    ], p });
}

const EIA_STATE = { low: "under forecast", normal: "within its range", no_range: "too few hours for its range",
  stale: "not reporting", no_data: "no data", small: "under 1,000 MW, not assessed" };
function eiaRow(b) {
  const p = feat(`blackout:us-demand:${b.id.toLowerCase()}:`);
  const r = b.run;
  const w = r.worst;
  return row({ keys: [`eia-${b.id}`], tab: "power", label: "US demand under forecast (EIA-930)",
    title: `${b.name} (${b.id})`,
    badges: [kindBadge(p), badge(`${signedPct(w.change)} against expected`, "danger"), badge(`${r.hours} h`), badge("BO-IND-16", "doc")],
    lines: [
      b.area ? muted(`Balances ${b.area} (point on the map placed by Blackout, schematic).`) : null,
      h("p", { class: "line" }, `${fmt(w.demand)} MW in the hour to `, timeEl(w.hour), `, against a day-ahead forecast of ${fmt(w.forecast)} MW. ` +
        `Its demand usually runs at ${Math.round(b.median_ratio * 100)}% of its forecast, so about ${fmt(w.expected)} MW was expected.`),
      muted(`${r.hours} hours in a row ${Math.round(b.line * 100)}% or more under expected, `, timeEl(r.start), " to ", timeEl(r.end),
        r.ongoing ? ", through the newest hour read" : "", `; about ${fmt(r.shortfall_mwh)} MWh less than expected.`),
      muted("Reported by the balancing authority to EIA; preliminary, and corrected within days. Blackout measures the gap, not its cause."),
    ], p });
}
function eiaTable(e) {
  const shown = e.bas.filter((b) => b.state !== "small");
  const small = e.bas.filter((b) => b.state === "small");
  return h("details", { class: "below" }, h("summary", {}, `Every balancing authority read (${shown.length})`),
    table("Balancing authorities: newest hour against what the forecast implies", ["Balancing authority", "Newest hour", "Line", "State"],
      shown.map((b) => tr([`eia-${b.id}`], "power", [`${b.name} (${b.id})`,
        b.latest ? [signedPct(b.latest.change), " at ", timeEl(b.latest.hour, "time")] : "not read",
        b.line != null ? `\u2212${Math.round(b.line * 100)}%` : "", EIA_STATE[b.state] || b.state]))),
    small.length ? muted(`Not assessed (median forecast under 1,000 MW): ${small.map((b) => b.id).join(", ")}.`) : null);
}

function odinStateRow(s, o) {
  const p = feat(`blackout:us-power:${s.state.toLowerCase()}:`);
  const over = s.meters > (o.threshold || 50000);
  return row({ keys: [`us-${s.state}`], tab: "power", label: "US outage (ODIN)",
    title: `${s.name}: ${fmt(s.meters)} meters without power`,
    badges: [over ? kindBadge(p, "the utilities") : badge("below the 50,000 line"), badge(plural(s.counties, "county", "counties")), over ? badge("BO-IND-14", "doc") : null],
    lines: [muted("Most in " + s.top.map((c) => `${c.name} (${fmt(c.meters)})`).join(", ") + "."),
      muted(`Utilities: ${s.utilities.slice(0, 4).join(", ")}${s.utilities.length > 4 ? ` and ${s.utilities.length - 4} more` : ""}.`),
      muted("As the utilities report it through ODIN, read at ", timeEl(o.read_at), ". Only utilities that report to ODIN are counted."),
      s.base_rate ? muted(`Base rate: ${s.base_rate.text} ${s.base_rate.caveat}`) : null],
    p: over ? p : null });
}
// How often EAGLE-I's history (2018-2025) had the state over 50,000 customers for an hour or more, as a table cell.
function baseRateCell(s) {
  const b = s.base_rate;
  if (!b) return "not in the tables";
  const r = b.per_year;
  // A state's rate counts only the years EAGLE-I covered it (Puerto Rico: 2021 on); name a shorter span.
  const span = b.years && b.years[0] > 2018 ? ` (${b.years[0]}–${b.years[1]})` : "";
  return (r === 0 ? "never" : r < 1 ? `once in ${Math.round(1 / r)} years` : `${r >= 20 ? Math.round(r / 5) * 5 : Math.round(r)} a year`) + span;
}
function odinTables(o) {
  const c = o.coverage;
  return [
    c ? muted(`Coverage at this reading: ${fmt(c.utilities)} utilities report to ODIN (${fmt((c.by_resolution || {}).COUNTY || 0)} by county), ` +
      `${fmt(c.with_error)} with a reporting error, ${fmt(c.stale)} not heard from in ${c.stale_hours} h; ${fmt(c.with_outages)} show outages` +
      ((c.outages_not_in_county_view || []).length ? `, ${c.outages_not_in_county_view.length} of them not by county (${c.outages_not_in_county_view.slice(0, 3).map((u) => u.name).join(", ")}${c.outages_not_in_county_view.length > 3 ? ", \u2026" : ""}), so not placed` : "") + ".")
      : muted("ODIN's utility status could not be read this run, so its coverage is not stated."),
    h("details", { class: "below" }, h("summary", {}, `Every state with an outage (${o.states.length})`),
      table("States with meters out at ODIN's reporting utilities", ["State", "Meters out", "Counties", "Most in", "Over 50,000 (EAGLE-I, 2018–2025)"],
        o.states.map((s) => tr([`us-${s.state}`], "power", [s.name, fmt(s.meters), fmt(s.counties), s.top.map((x) => x.name).join(", "), baseRateCell(s)]))),
      muted("The last column is how often EAGLE-I's history (Oak Ridge National Laboratory, 2018–2025) had the state over 50,000 customers out for an hour or more: the base rate, not this reading.")),
    h("details", { class: "below" }, h("summary", {}, `Every county with an outage (${o.counties.length})`),
      table("Counties with meters out", ["County", "State", "Meters out"],
        o.counties.map((x) => tr([`county-${x.fips}`], "power", [x.name, x.state, fmt(x.meters)])))),
  ];
}

const NIGHT_STATE = { darker: "darker", normal: "within its recent nights", too_dark: "too dark to measure a drop",
  stale: "newest clear night too old", no_baseline: "too few clear nights yet", no_clear_night: "no clear night",
  not_read: "not read" };
function nightRow(c) {
  const a = c.assessment;
  const p = feat(`blackout:night-lights:${c.id}:`);
  return row({ keys: [`night-${c.id}`], tab: "power", label: "Night lights (NASA Black Marble)",
    title: `${c.name}: night lights ${Math.round(-a.change * 100)}% below its recent clear nights`,
    badges: [kindBadge(p), badge(signedPct(a.change), "danger"), badge("BO-IND-15", "doc")],
    lines: [p ? h("p", { class: "line" }, p.summary) : null,
      muted("Night of ", day(a.latest.date), ` · ${Math.round(a.latest.clear * 100)}% of the box clear · median of ${a.baseline_nights} clear nights before: ${a.baseline_median} nW/cm\u00b2/sr`)],
    p });
}
function nightTable(nl) {
  return h("details", { class: "below" }, h("summary", {}, `Every watched city (${nl.cities.length})`),
    table("Watched cities: newest clear night against the clear nights before", ["City", "Newest clear night", "Change", "State"],
      nl.cities.map((c) => {
        const a = c.assessment || {};
        return tr([`night-${c.id}`], "power", [`${c.name}, ${countryName(c.country)}`, a.latest ? day(a.latest.date) : "none",
          a.change != null ? signedPct(a.change) : "", NIGHT_STATE[a.state] || a.state]);
      })),
    ...((nl.nights_gone || []).length ? [muted(`Not available from NASA this run: the night${nl.nights_gone.length > 1 ? "s" : ""} of `,
      ...nl.nights_gone.flatMap((d, i) => (i ? [", ", day(d)] : [day(d)])),
      ". NASA's near-real-time server keeps a night about a week and its standard archive hasn't reached it yet, so it is " +
      "left out, not filled in.")] : []));
}

function hijackRow(x) {
  const p = feat(`blackout:bgp-hijack:${x.radar_id}`, true);
  const n = (x.prefixes || []).length;
  const victims = x.victim_asns.slice(0, 3).map((v, i) => `AS${v}` + (x.victim_names[i] ? ` (${x.victim_names[i]})` : "")).join(", ") +
    (x.victim_asns.length > 3 ? ` and ${x.victim_asns.length - 3} more` : "");
  return row({ keys: [`hijack-${x.radar_id}`], tab: "routing", label: "BGP hijack (Cloudflare Radar)",
    title: `AS${x.hijacker_asn}${x.hijacker_name ? ` (${x.hijacker_name})` : ""} originated ${plural(n, "prefix", "prefixes")} of ${victims}`,
    badges: [kindBadge(p, "Cloudflare"), badge(`confidence ${x.confidence}`), badge(`${x.peers} peers`), badge("BO-IND-11", "doc")],
    lines: [muted("Prefixes: " + x.prefixes.slice(0, 6).join(", ") + (n > 6 ? " and more" : "")),
      muted("First seen ", timeEl(x.start), ", last seen ", timeEl(x.last_seen),
        (x.victim_countries || []).length ? ` · victim networks in ${x.victim_countries.map(countryName).join(", ")}` : ""),
      muted(`Cloudflare's evidence: ${(x.evidence || []).map(human).join(", ") || "none listed"}. Routing data alone doesn't tell a deliberate hijack from an operator error or a legitimate change the detector misreads.`)],
    p });
}
function leakGroups(b) {
  const by = new Map();
  for (const lk of b.notable_leaks || []) {
    if (!by.has(lk.leak_asn)) by.set(lk.leak_asn, []);
    by.get(lk.leak_asn).push(lk);
  }
  return [...by.entries()].map(([asn, leaks]) => ({ asn, leaks: leaks.sort((a, c) => (a.start < c.start ? -1 : 1)) }));
}
function leakRow({ asn, leaks }) {
  const p = feat(`blackout:bgp-leak:as${asn}:`);
  const top = leaks.reduce((m, x) => ((x.prefixes || 0) > (m.prefixes || 0) ? x : m), leaks[0]);
  const open = leaks.filter((x) => !x.finished).length;
  const path = top.segment.map((a, i) => `AS${a}` + (top.segment_names[i] ? ` (${top.segment_names[i]})` : "")).join(" > ");
  return row({ keys: [`leak-as${asn}`], tab: "routing", label: "Route leak (Cloudflare Radar)",
    title: `AS${asn}${leaks[0].leak_name ? ` (${leaks[0].leak_name})` : ""} leaked up to ${fmt(top.prefixes)} prefixes` + (leaks.length > 1 ? ` in ${leaks.length} events` : ""),
    badges: [kindBadge(p, "Cloudflare"), badge(open ? `${open} still open` : "finished", open ? "warn" : ""), badge(`${top.peers} peers`), badge("BO-IND-12", "doc")],
    lines: [muted(`Largest: ${fmt(top.prefixes)} prefixes from ${fmt(top.origins)} origin networks. Path (upstream > leaker > customer): ${path}.`),
      muted("Since ", timeEl(leaks[0].start), top.leak_country ? ` · leaker in ${countryName(top.leak_country)}` : ""),
      muted("A leak can send traffic the long way round or drop it; most are export mistakes. Reported by Cloudflare; Blackout does not assess intent.")],
    p });
}

// ---------------------------------------------------------------- country names (snapshot first, then the map shapes)
let COUNTRY_NAMES = {};
function countryName(cc) { return cc ? COUNTRY_NAMES[cc] || cc : ""; }

// ---------------------------------------------------------------- tabs
function renderBanner() {
  const snap = S.snap;
  const b = $("mode-banner");
  const problems = (snap.collection && snap.collection.problems) || [];
  b.replaceChildren();
  b.className = "mode-banner";
  if (snap.mode !== "live") {
    b.append(h("strong", {}, "Test data. "), "Everything on this page comes from made-up seed records used to check the pipeline (feed status ",
      h("code", {}, "test"), "). None of it is an observation.");
    b.hidden = false;
  } else if (problems.length) {
    const srcs = [...new Set(problems.map((p) => String(p.source || "unknown").split(":")[0]))];
    b.classList.add("problems");
    b.append(h("strong", {}, "Incomplete collection. "), `${problems.length} problem(s) reading ${srcs.join(", ")}; missing data is left out, not filled in. Details in the `,
      h("a", { href: "data/snapshot.json" }, "snapshot JSON"), ".");
    b.hidden = false;
  } else {
    b.hidden = true;
  }
  $("as-of").replaceChildren("Data as of ", timeEl(snap.generated_at), `; ${NEXT_RUN}.`);
}

function setCount(id, n, hot) {
  const el = $(id);
  el.textContent = String(n);
  el.className = "count" + (hot ? " hot" : "");
  el.setAttribute("aria-label", `${n} item${n === 1 ? "" : "s"}`);
}

const faaHot = (e) => e.kind !== "equipment" || (e.cluster || []).length >= 2;
function renderTiles(m) {
  const judged = S.feats.filter((f) => f.properties.kind === "assessment").length;
  const drops = m.watch.filter((w) => w.indicator_id === "BO-IND-6").length;
  const ind1 = m.outages.filter((o) => o.indicator_id === "BO-IND-1").length;
  const powerN = laneFeats("power").filter((x) => x.p.tier !== "routine").length;
  // The ZNPP on a single line is a standing state, not news, so only a new loss marks the tile: a US balancing
  // authority, state or city, or the plant without off-site power.
  const powerHot = laneFeats("power").some((x) => x.key !== "znpp") || /without off-site power/.test((feat("blackout:znpp-power:") || {}).title || "");
  const routingN = laneFeats("routing").length;
  const tile = (href, k, v, note, hot) => ({ href, k, v, note, hot });
  const all = [
    tile("#outages", "Outages (IODA)", m.outages.length, m.outages.length ? `${ind1} at BO-IND-1, ${m.outages.length - ind1} single-source` : "none at an indicator", ind1 > 0),
    tile(judged ? "#" + firstJudgmentKey() : "#method", "Judgments", judged, "likely shutdowns and corridor outages", judged > 0),
    tile("#censorship", "Censorship (OONI)", m.ooni.length, "countries at or above 15%"),
    tile("#radar", "Cloudflare Radar", m.annotations.length + m.anomalies.length, `${m.annotations.length} outages, ${m.anomalies.length} anomalies`),
    tile("#power", "Power", powerN, powerNote(m), powerHot),
    tile("#routing", "Routing (BGP)", routingN, m.bgp ? `${fmt(m.bgp.counts.hijacks)} hijacks, ${fmt(m.bgp.counts.leaks)} leaks in 72 h; widely seen listed` : "not read", false),
    tile("#cables", "Cable corridor alerts", m.alerts.length, `of ${m.corridors.length} corridors watched`, m.alerts.length > 0),
    tile("#providers", "Provider incidents", m.incidents.length, m.quietIncidents.length
      ? `open on their own status pages; ${m.quietIncidents.length} more with no update in ${QUIET_DAYS}+ days`
      : "open on their own status pages"),
    tile("#providers", "Provider networks dropping", drops, `of ${m.watchlist.length} watched on IODA`, drops > 0),
    tile("#providers/faa", "FAA ground stops", m.faaStops.filter(faaHot).length, m.faa ? "US flights stopped: every airport, an airline or a system outage (24 h)" : "not read",
      m.faaStops.some((e) => faaHot(e) && e.in_effect)),
    tile("#providers/public", "Reported outages", m.publicSurges.length, m.bluesky || m.gdelt ? "US services the public or the press report down (Bluesky, news)" : "not read",
      m.publicSurges.some((x) => x.b && x.g))];
  // A tile with something in it stays a tile; the zeros fold into one "Quiet now" line, and a lane that wasn't read
  // says so there rather than reading as quiet.
  const busy = all.filter((t) => t.v > 0 || t.hot);
  const quiet = all.filter((t) => !(t.v > 0 || t.hot));
  $("tiles").replaceChildren(...busy.map((t) => h("a", { class: "tile" + (t.hot ? " hot" : ""), href: t.href },
    h("span", { class: "k" }, t.k), h("span", { class: "v" }, String(t.v)), t.note ? h("span", { class: "n" }, t.note) : null)));
  $("tiles").hidden = !busy.length;
  const q = $("quiet");
  q.replaceChildren();
  if (quiet.length) {
    q.append(h("strong", {}, "Quiet now: "), ...joinNodes(quiet.map((t) => h("a", { href: t.href }, t.note === "not read" ? `${t.k} (not read)` : t.k)), ", "),
      ". Nothing at these in this run.");
  }
  q.hidden = !quiet.length;
}

function powerNote(m) {
  const z = m.znpp && (m.znpp.stated_in || (m.znpp.state !== "not_stated" ? m.znpp : null));
  return joinNodes([z ? `ZNPP ${{ none: "without off-site power", single: "on one line" }[z.state] || z.state}` : null,
    m.eia ? `${m.eia.notable.length} US BAs under forecast` : null,
    m.odin ? `${m.odin.notable_states.length} states over 50,000 out` : null,
    m.night ? `${(m.night.darker || []).length} cities darker` : null], "; ").join("") || "not read";
}

function judgmentKey(p) { return p.id.startsWith("blackout:corridor:") ? p.id.split(":")[2] : (p.countries || [])[0]; }
function firstJudgmentKey() {
  const f = S.feats.find((x) => x.properties.kind === "assessment");
  return f ? judgmentKey(f.properties) : "method";
}

function renderNow(m) {
  const items = [];
  const add = (href, kind, text, detail) => items.push(h("li", {}, h("a", { href }, h("span", { class: "now-k" }, kind), h("span", { class: "now-t" }, text)), detail ? h("span", { class: "now-d muted" }, detail) : null));
  for (const f of S.feats.filter((x) => x.properties.kind === "assessment")) {
    const p = f.properties;
    add(`#${judgmentKey(p)}`, "Judgment", p.title, `${p.likelihood_term} (${Math.round((p.probability || 0) * 100)}%), ${p.confidence} confidence`);
  }
  for (const c of m.alerts) if (!S.feats.some((f) => f.properties.kind === "assessment" && f.properties.id.startsWith(`blackout:corridor:${c.corridor_id}:`))) add(`#${c.corridor_id}`, "Cable corridor", c.name, `mean -${c.mean_drop_pct}% in ${c.countries.join(", ")}`);
  const judgedCc = new Set(S.feats.filter((f) => f.properties.kind === "assessment").flatMap((f) => f.properties.countries || []));
  const outs = m.outages.filter((o) => !judgedCc.has(o.country));
  outs.slice(0, 3).forEach((o) => add(`#${o.country}`, "Outage", `${o.country_name}: -${o.drop_pct}% in IODA`, o.indicator_id === "BO-IND-4" ? "one signal only, BGP held (BO-IND-4)" : "BO-IND-1"));
  if (outs.length > 3) add("#outages", "Outages", `and ${outs.length - 3} more countries`, null);
  m.annotations.slice(0, 2).forEach((a) => add("#radar", "Cloudflare Radar", `${radarWhere(a)}: ${human(a.cause) || "cause not stated"}`, "as Cloudflare reports it"));
  for (const e of m.faaStops.filter(faaHot)) add(`#providers/faa-${slug(e.id)}`, "US flights", `FAA: ${faaTitle(e)}`, `${faaState(e)}; as the FAA reports it`);
  for (const x of m.publicSurges) add(`#providers/${x.bKey || x.gKey}`, "Reported outage", `${x.label}: reported down`,
    joinNodes([x.b ? `${fmt(x.b.matches)} Bluesky posts` : null, x.g ? `${fmt(x.g.headlines)} headlines, ${fmt(x.g.outlets)} outlets` : null], ", ").join("") + "; not confirmed by the company");
  if (m.concurrency) add("#providers", "Providers", `Outages at ${m.concurrency.providers.length} providers began within 2 h: ${m.concurrency.provider_labels.join(", ")}`, "timing only; not shown to share a cause");
  if (m.incidents.length) {
    const by = {};
    m.incidents.forEach((i) => { by[i.provider_label] = (by[i.provider_label] || 0) + 1; });
    add("#providers", "Providers", `${m.incidents.length} open incident${m.incidents.length === 1 ? "" : "s"} on providers' own status pages`,
      Object.entries(by).map(([k, v]) => `${k} ${v}`).join(", "));
  }
  for (const { p, key } of laneFeats("power").filter((x) => x.p.tier !== "routine")) {
    const k = key === "znpp" ? "Nuclear site" : key.startsWith("eia-") ? "US grid" : key.startsWith("us-") ? "US outages" : "Night lights";
    add(`#power/${key}`, k, p.title, p.status_class === "reported" && !key.startsWith("night-") && !key.startsWith("eia-") ? "as the source reports it" : "Blackout's measurement");
  }
  const rt = laneFeats("routing");
  if (rt.length) {
    const hj = rt.filter((x) => x.key.startsWith("hijack-")).length;
    add("#routing", "Routing", `${plural(hj, "widely seen hijack")} and ${plural(rt.length - hj, "network")} leaking 100+ prefixes`, "as Cloudflare Radar reports them");
  }
  const drops = m.watch.filter((w) => w.indicator_id === "BO-IND-6");
  if (drops.length) add("#providers", "Provider networks", `${drops.length} watched network${drops.length === 1 ? "" : "s"} dropping on IODA`, drops.map((w) => `${w.label} -${w.drop_pct}%`).join(", "));
  if (m.ooni.length) add("#censorship", "Censorship", `${m.ooni.length} countr${m.ooni.length === 1 ? "y" : "ies"} at or above OONI's 15% line`, m.ooni.slice(0, 3).map((c) => `${c.country_name} ${pct1(c.anomaly_rate)}`).join(", ") + (m.ooni.length > 3 ? ", …" : ""));
  if (m.anomalies.length) {
    const where = {};
    m.anomalies.forEach((a) => { const k = countryName(a.country) + (a.asn ? ` AS${a.asn}` : ""); where[k] = (where[k] || 0) + 1; });
    add("#radar", "Cloudflare Radar", `${m.anomalies.length} verified traffic anomal${m.anomalies.length === 1 ? "y" : "ies"}`,
      Object.entries(where).map(([k, n]) => (n > 1 ? `${k} (${n})` : k)).join(", "));
  }
  $("now").replaceChildren(...(items.length ? items : [h("li", { class: "empty" }, "Nothing meets an indicator in this run. ", NEXT_RUN, ".")]));
}

function renderOutages(m) {
  const live = S.snap.mode === "live";
  $("outage-list").replaceChildren(...(m.outages.length ? m.outages.map(outageRow) : [emptyNote(
    "No country meets an IODA indicator in this run. A country appears here when IODA lists an outage event and its BGP or active-probing signal is down 25% or more for 30 minutes or longer (BO-IND-1), or when one signal drops while BGP holds (BO-IND-4). ",
    NEXT_RUN, ".")]));
  const rows = [...m.annotations.map(radarAnnotationRow), ...m.anomalies.map(radarAnomalyRow)];
  const r = S.snap.radar;
  const read = (r && r.read) || {};
  const unread = ["annotations", "anomalies"].filter((k) => read[k] === false);
  let empty;
  if (!r) empty = emptyNote(live ? "Cloudflare Radar was not read this run, so nothing can be said from it. " : "No Radar data in a seed build: Radar is never made up. ", NEXT_RUN, ".");
  else if (unread.length === 2) empty = emptyNote("Cloudflare Radar could not be read this run. ", NEXT_RUN, ".");
  else empty = emptyNote(`No Radar outage or verified anomaly in the last ${r.recent_hours || 72} h. An outage appears here when Cloudflare's analysts list one in the Radar Outage Center (BO-IND-9), and an anomaly when Cloudflare verifies one its detection flagged (BO-IND-10). `, NEXT_RUN, ".");
  $("radar-list").replaceChildren(...(rows.length ? rows : [empty]), ...(unread.length === 1 ? [emptyNote(`Not read this run: Radar ${unread[0]}.`)] : []));
  const nb = S.snap.netblocks;
  const nbRows = ((nb && nb.recent) || []).map(netblocksRow);
  $("netblocks-list").replaceChildren(...(nbRows.length ? nbRows : [emptyNote(
    !nb ? (live ? "NetBlocks was not read this run, so nothing can be said from it. " : "No NetBlocks data in a seed build: it is never made up. ")
      : "NetBlocks posted no report naming a place, platform or network in the last 72 h. NetBlocks chooses what it posts, so this does not mean there was no disruption. ",
    NEXT_RUN, ".")]));
}

function renderCensorship(m) {
  $("ooni-list").replaceChildren(...(m.ooni.length ? m.ooni.map(ooniRow) : [emptyNote(
    S.snap.mode === "live" && !(S.snap.censorship || []).length ? "OONI was not read this run, so nothing can be said from it. " :
      "No country is at or above 15% over 50 or more measurements in this run. A country appears here when OONI's confirmed blocking and anomalies together reach 15% (BO-IND-3). ",
    NEXT_RUN, ".")]));
  $("ooni-below").replaceChildren(...(m.ooniBelow.length ? [h("details", { class: "below" },
    h("summary", {}, `Measured, below the line (${m.ooniBelow.length})`),
    h("ul", { class: "plain" }, m.ooniBelow.map((c) => h("li", { "data-keys": keyOf(c.country), "data-tab": "censorship" },
      h("strong", {}, `${c.country_name} (${c.country})`), `: ${pct1(c.anomaly_rate)} over ${c.measurement_count.toLocaleString("en-US")} measurements` +
      (c.measurement_count < OONI_MIN ? " (too few to count)" : "")))))] : []));
}

function powerProblems(prefix) {
  const probs = ((S.snap.collection && S.snap.collection.problems) || []).filter((p) => String(p.source || "").startsWith(prefix));
  return probs.length ? [muted(`Not read this run: ${probs.slice(0, 3).map((p) => p.error).join("; ")}` + (probs.length > 3 ? ` and ${probs.length - 3} more` : ""))] : [];
}
function renderPower(m) {
  const live = S.snap.mode === "live";
  const none = (what) => emptyNote(live ? `Nothing was read from ${what} this run, so nothing can be said from it. ` : `Nothing from ${what} in a seed build: it is never made up. `, NEXT_RUN, ".");
  $("znpp").replaceChildren(...(m.znpp ? [znppRow(m.znpp)] : [none("the IAEA's updates")]), ...powerProblems("iaea"));
  const e = m.eia;
  const low = e ? e.bas.filter((b) => b.state === "low") : [];
  $("eia-list").replaceChildren(...(!e ? [none("EIA-930")] : [
    ...(low.length ? low.map(eiaRow) : [emptyNote(`No balancing authority's demand is 25% or more under what its forecast implies for 3 hours or more (BO-IND-16). ${e.assessed} of ${e.bas.length} read were assessed, through the hour to `, timeEl((e.bas.find((b) => b.window) || {}).window ? e.bas.find((b) => b.window).window.end : null), ". ", NEXT_RUN, ".")]),
    eiaTable(e)]), ...powerProblems("eia"));
  const o = m.odin;
  $("odin-list").replaceChildren(...(!o ? [none("ODIN")] : [
    ...(o.notable_states.length ? o.notable_states.map((s) => odinStateRow(s, o)) : [emptyNote(`No state above 50,000 meters out at ODIN's reporting utilities (BO-IND-14). ${fmt(o.meters)} meters out in ${plural(o.counties.length, "county", "counties")} and ${plural(o.states.length, "state")} at `, timeEl(o.read_at), "; the most in " + (o.states[0] ? `${o.states[0].name} (${fmt(o.states[0].meters)})` : "none") + ".")]),
    ...odinTables(o)]), ...powerProblems("odin"));
  const nl = m.night;
  const darker = nl ? nl.cities.filter((c) => (c.assessment || {}).state === "darker") : [];
  const read = nl ? nl.cities.filter((c) => (c.assessment || {}).state !== "not_read").length : 0;
  $("night-list").replaceChildren(...(!nl ? [none("NASA Black Marble")] : [
    ...(darker.length ? darker.map(nightRow) : [emptyNote(read ? `No watched city is 40% or more darker than its recent clear nights (BO-IND-15); ${read} of ${nl.cities.length} cities have nights read. ` : `No night was read for any of the ${nl.cities.length} watched cities this run. `, NEXT_RUN, ".")]),
    nightTable(nl)]), ...powerProblems("blackmarble"));
  $("power-radar").replaceChildren(...(m.radarPower.length ? m.radarPower.map(radarAnnotationRow) : [emptyNote(
    S.snap.radar ? `Cloudflare Radar lists no outage it attributes to a power outage in the last ${S.snap.radar.recent_hours || 72} h. `
      : live ? "Cloudflare Radar was not read this run. " : "No Radar data in a seed build: Radar is never made up. ", NEXT_RUN, ".")]));
}

function renderRouting(m) {
  const b = m.bgp;
  const live = S.snap.mode === "live";
  if (!b) {
    $("bgp-list").replaceChildren(emptyNote(live ? "Cloudflare Radar's BGP data was not read this run, so nothing can be said from it. " : "No routing data in a seed build: Radar is never made up. ", NEXT_RUN, "."));
    $("bgp-countries").replaceChildren();
    return;
  }
  const groups = leakGroups(b);
  const t = b.thresholds || {};
  const rows = [...(b.notable_hijacks || []).map(hijackRow), ...groups.map(leakRow)];
  const unread = Object.entries(b.read || {}).filter(([, ok]) => !ok).map(([k]) => k);
  $("bgp-list").replaceChildren(
    muted(`Last ${b.recent_hours || 72} h: ${plural(b.counts.hijacks, "high-confidence hijack")} and ${plural(b.counts.leaks, "route leak")} in Radar` +
      (b.monitors ? `, seen from its ${fmt(b.monitors)} route-collector peers` : "") +
      `. Listed here: hijacks seen by ${t.visible_peers || 25}+ peers and leaks of ${t.leak_prefixes || 100}+ prefixes seen by ${t.visible_peers || 25}+ peers.`),
    ...(rows.length ? rows : [emptyNote("No hijack or leak meets those lines in this window. ", NEXT_RUN, ".")]),
    ...(unread.length ? [emptyNote(`Not read this run: Radar BGP ${unread.join(" and ")}.`)] : []));
  const ccs = b.by_country || [];
  const ctr = (c) => tr([`bgp-${c.country}`], "routing", [`${countryName(c.country)} (${c.country})`, fmt(c.hijacks), fmt(c.leaks)]);
  const head = ["Country", "Hijacks of networks there", "Leaks by networks there"];
  $("bgp-countries").replaceChildren(...(ccs.length ? [
    table("Hijacks and leaks by country, top 10", head, ccs.slice(0, 10).map(ctr)),
    ccs.length > 10 ? h("details", { class: "below" }, h("summary", {}, `Every country (${ccs.length})`), table("Hijacks and leaks by country", head, ccs.slice(10).map(ctr))) : null,
  ].filter(Boolean) : [emptyNote("No hijack or leak placed in a country in this window.")]));
}

function renderContext(m) {
  const st = m.stop;
  const tl = m.testlists;
  const kids = [];
  if (st) {
    const row3 = (c) => tr([`stop-${c.country}`], "censorship", [`${c.name} (${c.country})`, fmt(c.shutdowns), (c.causes || []).join(", ")]);
    kids.push(muted(`Access Now's STOP sheet, ${st.year}: ${plural(st.countries.length, "country", "countries")}, ${fmt(st.countries.reduce((n, c) => n + c.shutdowns, 0))} shutdowns.`),
      h("details", { class: "below" }, h("summary", {}, `Shutdowns by country, ${st.year} (${st.countries.length})`),
        table(`Shutdowns Access Now documented in ${st.year}`, ["Country", "Shutdowns", "Stated causes (top 3)"], st.countries.map(row3))));
  } else {
    kids.push(emptyNote(S.snap.mode === "live" ? "Access Now's STOP sheet was not read this run." : "No STOP data in a seed build."));
  }
  if (tl && tl.global) {
    const per = Object.entries(tl.countries || {});
    kids.push(muted(`Citizen Lab's test lists: the global list holds ${fmt(tl.global.urls)} URLs in ${Object.keys(tl.global.categories || {}).length} categories; ` +
      `country lists read for ${per.map(([cc, l]) => `${cc} ${l ? fmt(l.urls) : "none"}`).join(", ") || "no country"}.`));
  } else {
    kids.push(emptyNote(S.snap.mode === "live" ? "Citizen Lab's test lists were not read this run." : "No test-list data in a seed build."));
  }
  $("censor-context").replaceChildren(...kids);
}

function renderCables(m) {
  $("corridors").replaceChildren(...(m.alerts.length ? m.alerts.map(corridorAlertRow) : [emptyNote(
    `No corridor alert in this run across the ${m.corridors.length} corridors watched. One appears when two or more countries served by the same corridor drop 20% or more within 2 hours of each other (BO-IND-2), or when a drop meets a suite tip within 50 km of a corridor point (BO-IND-5). `,
    NEXT_RUN, ".")]));
  const alertBy = Object.fromEntries(m.alerts.map((a) => [a.corridor_id, a]));
  $("corridor-register").replaceChildren(...m.corridors.map((c) => corridorCard(c, alertBy[c.id])));
  $("tips-list").replaceChildren(...(m.tips.length ? m.tips.map(tipRow) : [emptyNote(
    "No tip from the other programs in this run. Elements' earthquakes and volcanoes near a corridor appear here as Elements publishes them; Wake's AIS loitering and anchor-drag tips will once Wake is live; Vigil's attacks on telecom carriers when it reports one. ",
    NEXT_RUN, ".")]));
}

function renderProviders(m) {
  const live = S.snap.mode === "live";
  $("faa-stops").replaceChildren(...(m.faaStops.length ? m.faaStops.map(faaRow) : [emptyNote(
    !m.faa ? (live ? "The FAA's advisories could not be read this run, so nothing can be said. " : "The FAA's advisories are not read in a seed build. ")
      : `No ground stop for every airport, a whole airline, or an equipment or communications outage in the FAA's advisories of ${(m.faa.days || []).join(" and ")} (UTC). `,
    NEXT_RUN, ".")]));
  const notRead = [!m.bluesky ? "Bluesky" : null, !m.gdelt ? "GDELT" : null].filter(Boolean);
  $("public-reports").replaceChildren(...(m.publicSurges.length ? m.publicSurges.map((x) => publicRow(x, m)) : [emptyNote(
    !m.bluesky && !m.gdelt ? (live ? "Neither Bluesky nor GDELT could be read this run, so nothing can be said. " : "Bluesky and GDELT are not read in a seed build. ")
      : `No watched service is surging in public posts or news headlines${notRead.length ? ` (${notRead.join(" and ")} not read this run)` : ""}. Quiet here is not proof a service is up. `,
    NEXT_RUN, ".")]), publicTable(m));
  $("provider-concurrency").replaceChildren(...(m.concurrency ? [concurrencyRow(m.concurrency)] : []));
  const anyRead = m.feeds.some((f) => f.read);
  const quiet = m.quietIncidents.length ? [h("details", { class: "below" },
    h("summary", {}, `Long-running: open, with no update in ${QUIET_DAYS} days or more (${m.quietIncidents.length})`),
    ...m.quietIncidents.map(incidentRow))] : [];
  $("provider-incidents").replaceChildren(...(m.incidents.length ? m.incidents.map(incidentRow) : [emptyNote(
    !m.feeds.length ? (live ? "No status feed was read this run, so nothing can be said. " : "No status feeds are read in a seed build: a made-up incident at a real provider is too easy to misread. ")
      : quiet.length ? `None updated in the last ${QUIET_DAYS} days on the feeds that were read. `
      : anyRead ? "None listed as open on the feeds that were read. Nothing open is not proof a provider is up (see How this is measured). " : "No status feed could be read this run, so nothing can be said. ",
    NEXT_RUN, ".")]), ...quiet);
  const nets = m.watchlist.map(watchRow);
  $("asn-watch").replaceChildren(...(nets.length ? [...nets.filter((e) => e.matches(".row")),
    h("div", { class: "feeds" }, nets.filter((e) => !e.matches(".row")))] : [emptyNote("No watched networks configured.")]));
  $("provider-feeds").replaceChildren(...(m.feeds.length ? m.feeds.map(feedRow) : [emptyNote(live ? "No status feed was read this run. " : "No status feeds are read in a seed build. ", NEXT_RUN, ".")]));
}

// Satellite networks (blackout/satcom.py): the register, searchable and filtered by class with nothing excluded, and
// this run's traffic as a percent of each network's own normal.
const SAT_CLASS = { satellite_internet: "Satellite internet", satellite_maritime_aviation: "Maritime and aviation",
  satellite_operator_other: "Operator, other", satellite_tv: "Satellite TV", not_satellite: "Not satellite" };
const satState = { q: "", classes: null };
function satPct(m) {
  if (!m) return "not read";
  if (m.pct == null) return m.why || "no normal";
  return m.pct.toFixed(1) + "%";
}
function satTraffic(sat) {
  const t = sat && sat.traffic;
  return new Map(((t && t.networks) || []).map((r) => [r.asn, r]));
}
function satRadarCell(r) {
  if (!r || !r.radar) return "not read this run";
  if (r.radar.seen === false) return "Radar sees no traffic from it";
  if (r.radar.seen == null) return "Radar could not be read";
  return "worldwide " + satPct(r.radar.world);
}
function satIodaCell(r) {
  const io = r && r.ioda;
  if (!io) return "";
  const low = io.low_count || {};
  const one = (v, ds, label) => (v != null ? `${label} ${v.toFixed(1)}%` : low[ds] != null ? `${label} too few to measure` : null);
  return [one(io.bgp_pct, "bgp", "BGP"), one(io.probing_pct, "ping-slash24", "active probing")].filter(Boolean).join(", ") || "no signal";
}
function satNowRows(sat) {
  const out = [];
  for (const r of satTraffic(sat).values()) {
    const rd = r.radar || {};
    if (!rd.seen) continue;
    const share = new Map((rd.top_locations || []).map((x) => [x.cc, x]));
    if (rd.world && rd.world.pct != null) out.push({ r, cc: null, m: rd.world });
    for (const [cc, m] of Object.entries(rd.countries || {})) if (m && m.pct != null) out.push({ r, cc, m, share: share.get(cc) });
  }
  return out.sort((a, b) => a.m.pct - b.m.pct);
}
function renderSatNow(sat) {
  const t = sat && sat.traffic;
  const box = $("sat-now");
  if (!t) {
    box.replaceChildren(emptyNote(S.snap.mode === "live" ? "Satellite traffic could not be read this run. " : "Satellite traffic is not read in a seed build. ", NEXT_RUN, "."));
    return 0;
  }
  const rows = satNowRows(sat).map(({ r, cc, m, share }) => tr([`as${r.asn}`, cc], "satcom", [
    h("span", {}, `${r.operator} `, h("span", { class: "muted" }, `AS${r.asn}`)),
    cc ? `${(share && share.name) || countryName(cc)}${share ? ` (${share.share_pct}% of its requests)` : ""}` : "Worldwide",
    h("strong", {}, satPct(m)),
    h("span", {}, timeEl(m.current_from), " to ", timeEl(m.current_to)),
  ]));
  const unread = [!t.read.radar ? "Radar" : null, !t.read.ioda ? "IODA" : null].filter(Boolean);
  box.replaceChildren(
    h("p", { class: "one" }, `Read `, timeEl(t.checked_at), `. Normal: the same hours on each of the ${t.normal_days} days before.`,
      unread.length ? ` ${unread.join(" and ")} could not be read for every network this run (see the banner).` : ""),
    rows.length ? table("Satellite traffic now, lowest first", ["Network", "Where", "Now, % of normal", "Hours (now)"], rows)
      : emptyNote("No satellite network had a measurable series this run."));
  return rows.length;
}
function renderSatRegister(sat) {
  const traffic = satTraffic(sat);
  const q = satState.q.trim().toLowerCase().replace(/^as/, "");
  const shown = sat.networks.filter((n) => satState.classes.has(n.class) &&
    (!q || [n.asn, n.operator, n.parent, n.service, n.constellation, SAT_CLASS[n.class]].join(" ").toLowerCase().includes(q)));
  $("sat-shown").textContent = `Showing ${shown.length} of ${sat.networks.length} networks.`;
  const rows = shown.map((n) => {
    const r = traffic.get(n.asn);
    return tr([`as${n.asn}`], "satcom", [
      h("span", {}, ext(n.link, `AS${n.asn}`)),
      h("span", {}, n.operator, n.parent && n.parent !== n.operator ? h("span", { class: "muted" }, ` (${n.parent})`) : null),
      SAT_CLASS[n.class] || human(n.class),
      [human(n.service), n.constellation].filter((x) => x && x !== "unknown").join(", ") || "not stated",
      `${n.confidence} (${n.registry})`,
      sat.counted.includes(n.class) ? satRadarCell(r) : "not counted",
      satIodaCell(r),
    ]);
  });
  $("sat-register").replaceChildren(rows.length ? table("Satellite networks register",
    ["Network", "Operator", "Class", "Service, orbit", "Checked (registry)", "Radar now", "IODA now"], rows)
    : emptyNote("No network matches that search and those classes."));
}
function renderSatcom() {
  const sat = S.snap && S.snap.satcom;
  if (!sat || !sat.networks) {
    $("sat-now").replaceChildren(emptyNote("The satellite networks register is not in this build."));
    return;
  }
  if (!satState.classes) satState.classes = new Set(sat.classes);
  renderSatNow(sat);  // no count on the tab: these are levels, not items
  $("sat-classes").replaceChildren(...sat.classes.map((c) => h("label", { class: "sat-class" },
    h("input", { type: "checkbox", value: c, checked: satState.classes.has(c), onchange: (e) => {
      if (e.target.checked) satState.classes.add(c); else satState.classes.delete(c);
      renderSatRegister(sat);
    } }), ` ${SAT_CLASS[c] || human(c)} (${sat.networks.filter((x) => x.class === c).length})`)));
  const qBox = $("sat-q");
  if (!qBox.dataset.bound) {
    qBox.dataset.bound = "1";
    qBox.addEventListener("input", () => { satState.q = qBox.value; renderSatRegister(sat); });
  }
  renderSatRegister(sat);
  $("sat-notes").replaceChildren(`${sat.note} Register checked `, day(sat.checked_at), ". ",
    sat.not_found.length ? `Operators with no network found: ${sat.not_found.join("; ")}. ` : "",
    sat.name_matches_not_satellite.length ? `Name matches that are not satellite networks, listed apart: ${sat.name_matches_not_satellite.map((x) => `AS${x.asn} (${x.reason})`).join("; ")}.` : "");
}

// Trends (data/trends.json, blackout/trends.py): 7, 14 and 30-day windows against the window before, world and per
// country, read from IODA's, Radar's and NetBlocks' own archives each live run. Counts of what the sources report,
// never a judgment; IODA BGP-only flags that never clear ("standing") are listed apart and left out of the hours.
// Loaded the first time the tab is shown (about 400 KB).
const trendState = { w: "7", q: "", doc: null, loading: null };
const num1 = (x) => (x == null ? "not read" : Number(x).toLocaleString("en-US", { maximumFractionDigits: 1 }));
function trendChange(cur, prev) {
  if (cur == null || prev == null) return "not read";
  const d = Math.round((cur - prev) * 10) / 10;
  const sign = d > 0 ? "+" : d < 0 ? "−" : "";
  const ratio = prev > 0 && cur > 0 ? ` (${(cur / prev).toFixed(1)}×)` : "";
  return d === 0 ? "no change" : `${sign}${num1(Math.abs(d))}${ratio}`;
}
const REGION = (() => { try { return new Intl.DisplayNames(["en"], { type: "region" }); } catch { return null; } })();
const trendName = (doc, cc) => ((doc.countries || {})[cc] || {}).name || COUNTRY_NAMES[cc] || regionName(cc) || cc;
function regionName(cc) { try { return REGION ? REGION.of(cc) : null; } catch { return null; } }
const counted = (p) => (p.hours == null ? null : Math.round((p.hours - (p.standing_hours || 0)) * 10) / 10);
function trendWorldTable(t, n) {
  const c = t.current, p = t.previous;
  const rows = [
    ["IODA outage hours, standing flags left out", counted(c), counted(p)],
    ["IODA outage episodes", c.episodes, p.episodes],
    ["Countries with an outage hour (standing flags left out)", c.countries_with_outage, p.countries_with_outage],
    ["Cloudflare Radar annotated outages", c.radar_outages, p.radar_outages],
    ["Cloudflare Radar verified traffic anomalies", c.radar_anomalies, p.radar_anomalies],
    ["NetBlocks posts about connectivity", c.netblocks_connectivity, p.netblocks_connectivity],
    ["NetBlocks posts, all", c.netblocks_posts, p.netblocks_posts],
    ["IODA standing-flag hours (listed below, not counted above)", c.standing_hours, p.standing_hours],
    ["Countries with a standing flag", c.countries_standing, p.countries_standing],
  ].map(([label, a, b]) => tr([], "trends", [label, h("strong", {}, num1(a)), num1(b), trendChange(a, b)]));
  const lv = (x) => (x == null ? "none stated" : `${x}% of ordinary levels`);
  rows.push(tr([], "trends", ["Lowest level NetBlocks stated", h("strong", {}, lv(c.netblocks_lowest_level_pct)), lv(p.netblocks_lowest_level_pct), ""]));
  return table(`World, last ${n} days against the ${n} days before`, ["Measure", `Last ${n} days`, `${n} days before`, "Change"], rows);
}
// Daily bars over the 60 complete days: counted IODA hours, standing hours stacked lighter, the current window shaded.
function trendChart(doc, n, pick, label) {
  const NS = "http://www.w3.org/2000/svg";
  const days = trendDays(doc);
  const vals = days.map(pick);
  const max = Math.max(1, ...vals.map((v) => v.a + v.b));
  const W = 600, H = 120, bw = W / days.length;
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("class", "trend-svg");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", `${label}, each UTC day from ${days[0]} to ${days[days.length - 1]}; the last ${n} days shaded. The tables give the numbers.`);
  const rect = (x, y, w, hh, cls) => {
    const r = document.createElementNS(NS, "rect");
    for (const [k, v] of Object.entries({ x, y, width: w, height: hh, class: cls })) r.setAttribute(k, v);
    svg.append(r);
  };
  rect(W - n * bw, 0, n * bw, H, "trend-win");
  vals.forEach((v, i) => {
    const ha = (v.a / max) * (H - 4), hb = (v.b / max) * (H - 4);
    if (v.a) rect(i * bw + bw * 0.12, H - ha, bw * 0.76, ha, "trend-a");
    if (v.b) rect(i * bw + bw * 0.12, H - ha - hb, bw * 0.76, hb, "trend-b");
  });
  return h("figure", { class: "trend-fig" }, svg,
    h("figcaption", { class: "small muted" }, `${label}. Tallest bar ${num1(max)}. `, day(days[0]), " to ", day(days[days.length - 1]),
      `; the last ${n} days shaded.`));
}
function trendDays(doc) {
  const out = [];
  const end = new Date(doc.through + "T00:00:00Z");
  for (let i = 59; i >= 0; i--) out.push(new Date(end.getTime() - i * 86400000).toISOString().slice(0, 10));
  return out;
}
function worldDaily(doc) {
  const by = {};
  for (const c of Object.values(doc.countries || {})) {
    for (const [d, r] of Object.entries(c.days || {})) {
      const t = by[d] || (by[d] = { hours: 0, standing: 0, reports: new Set(), radar: new Set() });
      if (r.standing) t.standing += r.hours || 0; else t.hours += r.hours || 0;
      for (const o of r.radar_outages || []) t.radar.add(o.id);
      for (const id of r.netblocks_conn_ids || []) t.reports.add(id);
    }
  }
  return by;
}
function trendCountryRows(doc, n) {
  const exp = (e) => (e && e.internet_users_upper != null ? `${approxPeople(e.internet_users_upper)} (ITU ${e.year})` : "not given");
  const q = trendState.q.trim().toLowerCase();
  const list = Object.entries(doc.countries || {}).map(([cc, c]) => ({ cc, c, w: (c.windows || {})[n] })).filter(({ w }) => w)
    .filter(({ cc, c }) => !q || cc.toLowerCase() === q || String(c.name || "").toLowerCase().includes(q))
    .filter(({ w }) => [w.current, w.previous].some((p) => counted(p) || p.episodes || p.radar_outages || p.radar_anomalies || p.netblocks_posts || p.standing_hours))
    .sort((a, b) => (counted(b.w.current) || 0) - (counted(a.w.current) || 0) || (b.w.current.netblocks_connectivity || 0) - (a.w.current.netblocks_connectivity || 0)
      || a.cc.localeCompare(b.cc));
  const pair = (a, b) => h("span", {}, h("strong", {}, num1(a)), h("span", { class: "muted" }, ` / ${num1(b)}`));
  return { total: list.length, rows: list.map(({ cc, c, w }) => {
    const cu = w.current, pr = w.previous;
    const lv = cu.netblocks_lowest_level_pct;
    return tr([cc], "trends", [
      h("span", {}, c.name || countryName(cc), " ", h("span", { class: "muted" }, cc)),
      pair(counted(cu), counted(pr)),
      pair(cu.episodes, pr.episodes),
      h("span", {}, pair(cu.radar_outages, pr.radar_outages), cu.radar_anomalies || pr.radar_anomalies ? h("span", { class: "muted small" }, ` (anomalies ${num1(cu.radar_anomalies)} / ${num1(pr.radar_anomalies)})`) : null),
      h("span", {}, pair(cu.netblocks_connectivity, pr.netblocks_connectivity), c.netblocks_usual_30d != null ? h("span", { class: "muted small" }, ` (usual ${num1(c.netblocks_usual_30d)} per 30 days)`) : null),
      lv == null ? "none stated" : `${lv}%`,
      cu.standing_hours || pr.standing_hours ? pair(cu.standing_hours, pr.standing_hours) : "",
      exp(c.exposure),
    ]);
  }) };
}
function renderTrendCountries(doc) {
  const n = trendState.w;
  const { total, rows } = trendCountryRows(doc, n);
  $("trend-shown").textContent = `${total} ${total === 1 ? "country" : "countries"} with something reported in the last ${2 * n} days${trendState.q ? " matching the search" : ""}.`;
  $("trend-countries").replaceChildren(rows.length ? table(`Countries, last ${n} days against the ${n} days before`,
    ["Country", "Outage hours", "Episodes", "Radar outages", "NetBlocks connectivity posts", "Lowest level stated", "Standing-flag hours", "Exposure, an upper bound"], rows)
    : emptyNote("No country matches that search."));
}
function renderTrendStanding(doc) {
  const rows = [];
  for (const [cc, c] of Object.entries(doc.countries || {})) {
    for (const r of c.standing || []) rows.push({ cc, c, r });
  }
  rows.sort((a, b) => b.r.days - a.r.days || a.cc.localeCompare(b.cc));
  $("trend-standing").replaceChildren(rows.length ? table("IODA standing flags", ["Country", "From", "To", "Days", "Hours"],
    rows.map(({ cc, c, r }) => tr([cc], "trends", [h("span", {}, c.name || countryName(cc), " ", h("span", { class: "muted" }, cc)),
      r.first === doc.first_day ? h("span", {}, "on or before ", day(r.first)) : day(r.first), r.last === doc.today ? h("span", {}, day(r.last), " (still set)") : day(r.last), num1(r.days), num1(r.hours)])))
    : emptyNote("No standing flag in the 61 days read."));
}
function renderTrendSatcom(doc, n) {
  const sat = doc.satcom;
  const box = $("trend-satcom");
  if (!sat) { box.replaceChildren(emptyNote("The satellite networks were not read for this series.")); return; }
  const rows = [];
  for (const [asn, net] of Object.entries(sat.networks || {})) {
    const series = [[null, net.world], ...Object.entries(net.countries || {})];
    for (const [cc, s] of series) {
      const w = s && s.windows && s.windows[n];
      if (!w || w.current.mean_pct == null) continue;
      rows.push({ asn, net, cc, w });
    }
  }
  rows.sort((a, b) => a.w.current.mean_pct - b.w.current.mean_pct);
  const pct = (m) => (m.mean_pct == null ? "no value" : `${m.mean_pct.toFixed(1)}%`);
  box.replaceChildren(h("p", { class: "one" }, `${sat.basis}. Lowest first.`),
    rows.length ? table(`Satellite networks, mean percent of normal, last ${n} days against the ${n} days before`,
      ["Network", "Where", `Last ${n} days`, `${n} days before`, "Days with a value"],
      rows.map(({ asn, net, cc, w }) => tr([`as${asn}`, cc], "trends", [
        h("span", {}, `${net.operator} `, h("span", { class: "muted" }, `AS${asn}`)), cc ? trendName(doc, cc) : "Worldwide",
        h("strong", {}, pct(w.current)), pct(w.previous), `${w.current.days} / ${w.previous.days}`])))
      : emptyNote("No satellite network had a value in this window."));
}
function renderTrends() {
  const doc = trendState.doc;
  const n = trendState.w;
  if (!doc) {
    $("trend-world").replaceChildren(emptyNote(S.snap && S.snap.mode === "live"
      ? "The trend series could not be loaded. It is rebuilt every live run; " : "The trend series is built by live runs only. ", NEXT_RUN, "."));
    return;
  }
  const t = (doc.totals || {})[n];
  const unread = Object.entries(doc.read || {}).filter(([, v]) => !v).map(([k]) => human(k));
  const daily = worldDaily(doc);
  $("trend-asof").replaceChildren("Series built ", timeEl(doc.generated_at), ". Windows end on ", day(doc.through),
    ", the last complete UTC day.", unread.length ? ` Not read this run: ${unread.join(", ")} (shown as "not read").` : "",
    (doc.problems || []).length ? ` Problems: ${doc.problems.map((p) => p.source).join(", ")}.` : "");
  $("trend-world").replaceChildren(t ? trendWorldTable(t, n) : emptyNote("This window is longer than the series."),
    trendChart(doc, Number(n), (d) => ({ a: (daily[d] || {}).hours || 0, b: (daily[d] || {}).standing || 0 }),
      "World IODA outage hours a day (orange), with standing-flag hours stacked above (blue)"),
    trendChart(doc, Number(n), (d) => ({ a: ((daily[d] || {}).radar || new Set()).size, b: ((daily[d] || {}).reports || new Set()).size }),
      "Reports a day: Cloudflare Radar annotated outages (orange) and NetBlocks connectivity posts (blue)"));
  renderTrendCountries(doc);
  renderTrendStanding(doc);
  renderTrendSatcom(doc, n);
  const bursts = (doc.notes || {}).ioda_bursts_left_out || [];
  $("trend-bursts").replaceChildren(bursts.length ? h("ul", { class: "plain" }, bursts.map((b) =>
    h("li", {}, timeEl(b.start), `: ${b.datasource === "ping-slash24" ? "active probing" : b.datasource === "merit-nt" ? "darknet" : b.datasource.toUpperCase()}, ${b.countries} countries`)))
    : emptyNote("None in the 61 days read."));
  $("trend-notes").replaceChildren(h("p", {}, doc.method), h("p", {}, doc.exposure_basis, " ", doc.exposure_source, "."),
    h("p", {}, "Sources: ", doc.sources.join("; "), "."));
  if (T) T.renderAll($("panel-trends"));
}
function loadTrends() {
  if (trendState.loading) return trendState.loading;
  trendState.loading = getJSON("data/trends.json", true).then((doc) => {
    trendState.doc = doc && doc.format === "blackout-trends/1" ? doc : null;
    renderTrends();
    const key = keyOf(decodeURIComponent(location.hash.replace(/^#/, "")).split("/")[1] || "");
    if (key && !$("panel-trends").hidden) highlight("trends", key);
  });
  return trendState.loading;
}
function bindTrends() {
  for (const r of document.querySelectorAll("input[name=trend-w]")) {
    r.addEventListener("change", () => { if (r.checked) { trendState.w = r.value; renderTrends(); } });
  }
  const q = $("trend-q");
  q.addEventListener("input", () => { trendState.q = q.value; if (trendState.doc) renderTrendCountries(trendState.doc); });
}

function renderMethod() {
  const md = S.method;
  if (!md) {
    $("method-indicators").replaceChildren(emptyNote("The indicator register could not be loaded."));
    return;
  }
  $("method-indicators").replaceChildren(h("dl", { class: "dl wide" }, md.indicators.map((i) => [h("dt", {}, h("code", {}, i.id)), h("dd", {}, i.text)]),
    md.warning_problems.map((w) => [h("dt", {}, h("code", {}, w.id)), h("dd", {}, "Warning problem: " + w.title)])));
  $("method-sources").replaceChildren(h("details", { class: "how" }, h("summary", {}, `Every source registered (${md.sources.length})`),
    h("ul", { class: "plain" }, md.sources.map((s) => h("li", {}, h("strong", {}, s.name || s.id), s.grade ? ` (grade ${s.grade})` : "",
      s.licence ? `: ${s.licence}` : "", " ", ext(s.url, "link"))))));
}

// "Elsewhere today" (tic-related.js, from the hub): what the other layers report in the countries of Blackout's own
// current findings (any tier but routine), judgments first, then unusual readings, read from COP's picture.
// Hidden until COP's index is read.
function relatedCountries() {
  const order = { warning: 0, anomaly: 1 };
  const feats = S.feats.filter((f) => f.properties.tier !== "routine")
    .sort((a, b) => (order[a.properties.tier] ?? 2) - (order[b.properties.tier] ?? 2));
  const cc = new Set();
  for (const f of feats) for (const c of f.properties.countries || []) cc.add(c);
  return [...cc];
}
function renderRelated() {
  const el = $("tic-related");
  const countries = relatedCountries();
  if (!el || !window.TicRelated || !countries.length) { if (el) el.hidden = true; return; }
  window.TicRelated.render(el, { countries, here: "blackout", names: COUNTRY_NAMES, level: 3 });
}

function render() {
  const m = model();
  COUNTRY_NAMES = {};
  for (const f of (S.shapes && S.shapes.features) || []) COUNTRY_NAMES[f.properties.cc] = f.properties.name;
  for (const x of [...(S.snap.outages || []), ...(S.snap.censorship || [])]) if (x.country && x.country_name) COUNTRY_NAMES[x.country] = x.country_name;
  for (const f of S.feats) {
    const p = f.properties;
    if (/^blackout:radar-/.test(p.id) && (p.countries || []).length === 1) {
      const name = p.title.split(/[,:]/)[0];
      if (name && name !== p.countries[0] && !COUNTRY_NAMES[p.countries[0]]) COUNTRY_NAMES[p.countries[0]] = name;
    }
  }
  renderBanner();
  renderTiles(m);
  renderNow(m);
  renderRelated();
  renderOutages(m);
  renderCensorship(m);
  renderContext(m);
  renderPower(m);
  renderRouting(m);
  renderCables(m);
  renderProviders(m);
  renderSatcom();
  renderMethod();
  setCount("count-outages", m.outages.length + m.annotations.length + m.anomalies.length, m.outages.some((o) => o.indicator_id === "BO-IND-1"));
  setCount("count-censorship", m.ooni.length, false);
  setCount("count-power", laneFeats("power").filter((x) => x.p.tier !== "routine").length, false);
  setCount("count-routing", laneFeats("routing").length, false);
  setCount("count-cables", m.alerts.length + m.tips.length, m.alerts.length > 0);
  setCount("count-providers", m.incidents.length + m.watch.filter((w) => w.indicator_id === "BO-IND-6").length + (m.concurrency ? 1 : 0) + m.faaStops.filter(faaHot).length
    + m.publicSurges.length, m.faaStops.some((e) => faaHot(e) && e.in_effect) || m.publicSurges.some((x) => x.b && x.g));
  if (T) T.renderAll(document);
}

// ---------------------------------------------------------------- map panel: every item at a country or corridor
function mapKeys() {
  const m = model();
  const keys = new Set();
  m.outages.forEach((o) => keys.add(keyOf(o.country)));
  m.ooni.forEach((c) => keys.add(keyOf(c.country)));
  m.annotations.forEach((a) => (a.countries || []).forEach((cc) => keys.add(keyOf(cc))));
  m.anomalies.forEach((a) => a.country && keys.add(keyOf(a.country)));
  m.corridors.forEach((c) => keys.add(keyOf(c.id)));
  m.tips.forEach((t) => t.item_id && t.lat != null && keys.add(keyOf(t.item_id)));
  for (const tab of ["power", "routing"]) laneFeats(tab).forEach((x) => x.f.geometry && keys.add(x.key));
  (m.odin ? m.odin.counties : []).forEach((c) => keys.add(keyOf(`us-${c.state}`)));
  return keys;
}
// The row for a Power or Routing key, or null.
function laneRow(key) {
  const m = model();
  if (key === "znpp" && m.znpp) return { title: "Zaporizhzhya NPP", rows: [znppRow(m.znpp)] };
  if (key.startsWith("eia-") && m.eia) {
    const b = m.eia.bas.find((x) => keyOf(`eia-${x.id}`) === key);
    if (b) return { title: `${b.name} (${b.id})`, rows: b.state === "low" ? [eiaRow(b)] : [], note: b.state === "low" ? null : `Demand ${EIA_STATE[b.state] || b.state}.` };
  }
  if (key.startsWith("us-") && m.odin) {
    const s = m.odin.states.find((x) => keyOf(`us-${x.state}`) === key);
    if (s) return { title: s.name, rows: [odinStateRow(s, m.odin)] };
  }
  if (key.startsWith("night-") && m.night) {
    const c = m.night.cities.find((x) => keyOf(`night-${x.id}`) === key);
    if (c) return { title: c.name, rows: (c.assessment || {}).state === "darker" ? [nightRow(c)] : [], note: `Night lights ${NIGHT_STATE[(c.assessment || {}).state] || "not read"}.` };
  }
  if (key.startsWith("hijack-") && m.bgp) {
    const x = (m.bgp.notable_hijacks || []).find((y) => keyOf(`hijack-${y.radar_id}`) === key);
    if (x) return { title: "BGP hijack", rows: [hijackRow(x)] };
  }
  if (key.startsWith("leak-as") && m.bgp) {
    const g = leakGroups(m.bgp).find((y) => keyOf(`leak-as${y.asn}`) === key);
    if (g) return { title: "Route leak", rows: [leakRow(g)] };
  }
  return null;
}

function rowsForMapKey(key) {
  const m = model();
  const out = [];
  const cc = key.toUpperCase();
  const corridor = m.corridors.find((c) => keyOf(c.id) === key);
  if (corridor) {
    const alert = m.alerts.find((a) => a.corridor_id === corridor.id);
    return { title: corridor.name, rows: [alert ? corridorAlertRow(alert) : null, corridorCard(corridor, alert)].filter(Boolean),
      note: "Drawn schematically: each landing station joined to the corridor's reference point. These are not cable routes." };
  }
  const tip = m.tips.find((t) => keyOf(t.item_id) === key);
  if (tip) return { title: tip.title, rows: [tipRow(tip)] };
  const lane = laneRow(key);
  if (lane) return lane;
  m.outages.filter((o) => o.country === cc).forEach((o) => out.push(outageRow(o)));
  m.ooni.filter((c) => c.country === cc).forEach((c) => out.push(ooniRow(c)));
  m.annotations.filter((a) => (a.countries || []).includes(cc)).forEach((a) => out.push(radarAnnotationRow(a)));
  m.anomalies.filter((a) => a.country === cc).forEach((a) => out.push(radarAnomalyRow(a)));
  return { title: `${countryName(cc)} (${cc})`, rows: out };
}

function openPanel(key) {
  const panel = $("map-detail");
  const { title, rows, note } = rowsForMapKey(key);
  S.selected = key;
  for (const r of rows) {
    const tab = r.getAttribute("data-tab");
    const label = r.getAttribute("data-label");
    const rh = r.querySelector(".row-h");
    if (label && rh) rh.textContent = label;  // the panel's own heading already names the country
    r.append(h("p", { class: "line" }, h("a", { href: `#${tab}/${key}` }, `Open in ${TAB_NAME[tab]}`)));
  }
  panel.replaceChildren(...[
    h("div", { class: "detail-head" }, h("h3", { id: "map-detail-h" }, title),
      h("button", { type: "button", class: "btn", onclick: closePanel, "aria-label": "Close details" }, "Close")),
    note ? h("p", { class: "line muted" }, note) : null,
    ...(rows.length ? rows : [emptyNote("No current item here; it may have ended since the link was made.")])].filter(Boolean));
  panel.hidden = false;
  if (T) T.renderAll(panel);
  if (S.map) S.map.select(key);
}
function closePanel() {
  $("map-detail").hidden = true;
  S.selected = null;
  if (S.map) S.map.select(null);
  if (mapKeys().has(keyOf(location.hash.slice(1)))) history.replaceState(null, "", "#overview");
}

// ---------------------------------------------------------------- map (lazy)
function mapModel() {
  const m = model();
  const judged = S.feats.filter((f) => f.properties.kind === "assessment" && f.properties.id.startsWith("blackout:outage:")).flatMap((f) => f.properties.countries || []);
  const radarPts = S.feats.filter((f) => /^blackout:radar-/.test(f.properties.id) && f.geometry).map((f) => ({
    type: "Feature", geometry: f.geometry, properties: { key: keyOf((f.properties.countries || [])[0]) } })).filter((f) => f.properties.key);
  const alertIds = new Set(m.alerts.map((a) => a.corridor_id));
  return {
    shapes: S.shapes || { type: "FeatureCollection", features: [] },
    outageCcs: m.outages.map((o) => o.country), judgedCcs: judged, ooniCcs: m.ooni.map((c) => c.country),
    outagePts: { type: "FeatureCollection", features: m.outages.map((o) => ({ type: "Feature", geometry: { type: "Point", coordinates: [o.lon, o.lat] },
      properties: { key: keyOf(o.country), judged: judged.includes(o.country) } })) },
    radar: { type: "FeatureCollection", features: radarPts },
    corridors: m.corridors.map((c) => ({ id: c.id, key: keyOf(c.id), name: c.name, lat: c.lat, lon: c.lon, landing: c.landing_coords || [], alert: alertIds.has(c.id) })),
    tips: { type: "FeatureCollection", features: m.tips.filter((t) => t.lat != null && t.lon != null).map((t) => ({
      type: "Feature", geometry: { type: "Point", coordinates: [t.lon, t.lat] }, properties: { key: keyOf(t.item_id) } })) },
    power: { type: "FeatureCollection", features: laneFeats("power").filter((x) => x.f.geometry).map((x) => ({
      type: "Feature", geometry: x.f.geometry, properties: { key: x.key, routine: x.p.tier === "routine" } })) },
    odin: { type: "FeatureCollection", features: (m.odin ? m.odin.counties : []).map((c) => ({
      type: "Feature", geometry: { type: "Point", coordinates: [c.lon, c.lat] }, properties: { key: keyOf(`us-${c.state}`), meters: c.meters } })) },
    routing: { type: "FeatureCollection", features: laneFeats("routing").filter((x) => x.f.geometry).map((x) => ({
      type: "Feature", geometry: x.f.geometry, properties: { key: x.key } })) },
  };
}

function ensureMap() {
  if (S.map || S.mapLoading || S.mapFailed || !S.snap) return S.mapLoading;
  const note = (text) => { $("map").replaceChildren(h("p", { class: "map-fail" }, text)); };
  S.mapLoading = import("./map.js")
    .then((mod) => mod.createMap($("map"), mapModel(), { onPick: (key) => {
      history.replaceState(null, "", "#" + key);
      openPanel(key);
    }, layers: S.layers }))
    .then((api) => { S.map = api; if (S.selected) api.select(S.selected, { fly: S.flyOnReady }); S.flyOnReady = false; return api; })
    .catch((err) => {
      S.mapFailed = true;
      console.warn("Blackout map:", err);
      note("The map could not be drawn in this browser (it needs WebGL). Every item is listed in its tab.");
    });
  return S.mapLoading;
}

// ---------------------------------------------------------------- tabs and routing
const tabBtn = (t) => $("tab-" + t);
function showTab(tab, { focus } = {}) {
  document.body.dataset.tab = tab;
  for (const t of TABS) {
    const on = t === tab;
    tabBtn(t).setAttribute("aria-selected", String(on));
    tabBtn(t).tabIndex = on ? 0 : -1;
    $("panel-" + t).hidden = !on;
  }
  if (focus) tabBtn(tab).focus();
  // On a phone the tabs are one row that scrolls sideways: keep the selected one in view.
  const strip = $("views");
  if (strip.scrollWidth > strip.clientWidth) {
    const d = tabBtn(tab).getBoundingClientRect().left - strip.getBoundingClientRect().left;
    if (d < 0 || d + tabBtn(tab).offsetWidth > strip.clientWidth) strip.scrollLeft += d - 16;
  }
  if (tab === "trends") loadTrends();
  if (tab === "overview") {
    ensureMap();
    if (S.map) S.map.resize();
  }
  document.querySelectorAll(".row.hit, .reg.hit, .feed.hit, li.hit, tr.hit").forEach((e) => e.classList.remove("hit"));
}
function bringIntoView(el) {
  if (!el) return;
  const top = el.getBoundingClientRect().top;
  if (top < 0 || top > window.innerHeight * 0.8) el.scrollIntoView({ block: "start" });
}
function highlight(tab, key) {
  const hits = [...$("panel-" + tab).querySelectorAll("[data-keys]")].filter((e) => e.getAttribute("data-keys").split(" ").includes(key));
  hits.forEach((e) => {
    e.classList.add("hit");
    const d = e.querySelector("details.doctrine");
    if (d) d.open = true;
    for (let up = e.parentElement && e.parentElement.closest("details"); up; up = up.parentElement && up.parentElement.closest("details")) up.open = true;
  });
  if (hits[0]) {
    hits[0].tabIndex = -1;
    hits[0].scrollIntoView({ block: "center" });
    hits[0].focus({ preventScroll: true });
  }
  return hits.length;
}
function rowTabFor(key) {
  for (const tab of ["outages", "censorship", "power", "routing", "cables", "providers"]) {
    if ([...$("panel-" + tab).querySelectorAll("[data-keys]")].some((e) => e.getAttribute("data-keys").split(" ").includes(key))) return tab;
  }
  return null;
}

function route() {
  const raw = decodeURIComponent(location.hash.replace(/^#/, "")).trim();
  const [a, b] = raw.split("/");
  const key = keyOf(a);
  if (!key) { showTab("overview"); return; }
  if (TABS.includes(key)) {
    if (!S.selected) $("map-detail").hidden = true;  // drop a "no current item" notice once the reader moves on
    showTab(key);
    if (b && S.snap) highlight(key, keyOf(b));
    else bringIntoView($("views"));
    return;
  }
  if (SECTIONS[key]) {
    showTab(SECTIONS[key][0]);
    $(SECTIONS[key][1]).scrollIntoView({ block: "start" });
    return;
  }
  const anchor = document.getElementById(a);
  if (anchor && !anchor.closest("[role=tabpanel][hidden]")) { anchor.tabIndex = -1; anchor.focus(); return; }
  if (!S.snap) return;  // routed again once the data is in
  if (mapKeys().has(key) && !S.mapFailed) {
    showTab("overview");
    S.flyOnReady = true;
    openPanel(key);
    if (S.map) S.map.select(key, { fly: true });
    bringIntoView($("map"));
    return;
  }
  const tab = rowTabFor(key);
  if (tab) { showTab(tab); highlight(tab, key); return; }
  showTab("overview");
  $("map-detail").hidden = false;
  $("map-detail").replaceChildren(h("div", { class: "detail-head" }, h("h3", { id: "map-detail-h" }, `#${a}`),
    h("button", { type: "button", class: "btn", onclick: closePanel, "aria-label": "Close details" }, "Close")),
    emptyNote(`No current item for ${a}; it may have ended since the link was made. `, NEXT_RUN, "."));
}

function bindTabs() {
  const list = $("views");
  list.addEventListener("click", (e) => {
    const btn = e.target.closest("[role=tab]");
    if (!btn) return;
    const tab = btn.id.slice(4);
    if (location.hash === "#" + tab) route();
    else location.hash = tab;
  });
  list.addEventListener("keydown", (e) => {
    const i = TABS.indexOf(document.activeElement && document.activeElement.id ? document.activeElement.id.slice(4) : "");
    if (i < 0) return;
    let j = null;
    if (e.key === "ArrowRight") j = (i + 1) % TABS.length;
    else if (e.key === "ArrowLeft") j = (i - 1 + TABS.length) % TABS.length;
    else if (e.key === "Home") j = 0;
    else if (e.key === "End") j = TABS.length - 1;
    if (j == null) return;
    e.preventDefault();
    history.replaceState(null, "", "#" + TABS[j]);
    showTab(TABS[j], { focus: true });
  });
  $("map-layers").addEventListener("change", (e) => {
    const name = e.target.getAttribute("data-layer");
    if (!name) return;
    S.layers[name] = e.target.checked;
    if (S.map) S.map.setLayer(name, e.target.checked);
  });
  window.addEventListener("hashchange", route);
  // The map's layer list is folded on a phone (it would push the map down a screen) and open on wider screens.
  const fold = $("layers-fold");
  const wide = window.matchMedia("(min-width: 641px)");
  fold.open = wide.matches;
  wide.addEventListener("change", () => { if (wide.matches) fold.open = true; });
}

// ---------------------------------------------------------------- start
bindTabs();
bindTrends();
if (T) {
  T.mountSettings($("time-settings"));
  T.onChange(() => T.renderAll(document));
}
route();
try {
  const [snap, feed, method, shapes] = await Promise.all([getJSON("data/snapshot.json"), getJSON("data/feed.geojson", true),
    getJSON("data/method.json", true), getJSON("data/countries.geojson", true)]);
  S.shapes = shapes;
  S.snap = snap;
  S.feed = feed;
  S.feats = (feed && feed.features) || [];
  S.method = method;
  render();
  route();
  if (!$("panel-overview").hidden) ensureMap();
} catch (err) {
  const b = $("mode-banner");
  b.className = "mode-banner problems";
  b.textContent = `Could not load Blackout's data (${err && err.message ? err.message : "unknown error"}). Nothing below is current.`;
  b.hidden = false;
}
