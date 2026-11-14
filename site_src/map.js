// Blackout's map, on COP's stack: vendored MapLibre GL JS, Natural Earth land served from this site at world zoom,
// OpenFreeMap's vector map once zoomed in past DETAIL_ZOOM (its world tiles are large), and Natural Earth alone at
// every zoom if OpenFreeMap can't be reached. Colours are set here through MapLibre paint (no inline styles; CSP).
// What it draws: IODA outage countries filled, OONI countries outlined, Cloudflare Radar items as points, power items
// (the ZNPP, US balancing authorities, US states, darker cities) and ODIN's US counties as yellow points, widely seen
// BGP hijacks and leaks as dark points, suite tips as points, and cable corridors as schematic lines (each landing
// station joined to the corridor's reference point; not cable routes, and no TeleGeography data).
import * as ml from "./vendor/maplibre-gl/maplibre-gl.mjs";

const dark = matchMedia("(prefers-color-scheme: dark)");
const DETAIL_ZOOM = 3.5;
const LABEL_ZOOM = 2.2;
const BASE = {
  light: { style: "https://tiles.openfreemap.org/styles/positron", land: "#F2F3F0", water: "#C2C8CA", border: "#B9BEC2" },
  dark: { style: "https://tiles.openfreemap.org/styles/dark", land: "#0C0C0C", water: "#1B1B1D", border: "#3A3F46" },
};
// Okabe-Ito hues, shifted where needed so each mark's edge is at least 3:1 against the land or water under it.
// style.css repeats these for the layer key (--m-*).
const MARK = {
  light: { outage: "#D55E00", outageLine: "#8A3B00", ooni: "#A2367F", radar: "#0072B2", ring: "#FFFFFF",
    corr: "#7A5C00", corrAlert: "#8F5D00", casing: "#FFFFFF", tip: "#007A58", sel: "#14181F",
    power: "#F0E442", powerLine: "#4D4500", routing: "#1A1A1A" },
  dark: { outage: "#FF8A3D", outageLine: "#FFB27A", ooni: "#E7A3CC", radar: "#56B4E9", ring: "#0B0E14",
    corr: "#C9A227", corrAlert: "#F0B72F", casing: "#000000", tip: "#3DDC97", sel: "#FFFFFF",
    power: "#F0E442", powerLine: "#000000", routing: "#F2F2F2" },
};
const LAYERS = {  // page toggle -> map layers
  outages: ["bo-outage-fill", "bo-outage-line", "bo-outage-pt"],
  ooni: ["bo-ooni-line"],
  radar: ["bo-radar"],
  power: ["bo-odin", "bo-power"],
  routing: ["bo-routing"],
  corridors: ["bo-corr-sel", "bo-corr-casing", "bo-corr-line", "bo-corr-alert", "bo-corr-pts"],
  tips: ["bo-tips"],
};
const CLICK_POINTS = ["bo-tips", "bo-power", "bo-routing", "bo-outage-pt", "bo-radar", "bo-odin", "bo-corr-pts", "bo-corr-alert", "bo-corr-line"];

const theme = () => (dark.matches ? "dark" : "light");
const worldSource = () => ({ type: "geojson", data: new URL("data/world.geojson", location.href).href, maxzoom: 5, tolerance: 1 });
const worldLayers = (b, maxzoom) => [
  { id: "bo-land", type: "fill", source: "bo_world", ...(maxzoom ? { maxzoom } : {}), paint: { "fill-color": b.land } },
  { id: "bo-borders", type: "line", source: "bo_world", ...(maxzoom ? { maxzoom } : {}), paint: { "line-color": b.border, "line-width": 0.6 } },
];
function fallbackStyle() {
  const b = BASE[theme()];
  return { version: 8, sources: { bo_world: worldSource() },
    layers: [{ id: "bg", type: "background", paint: { "background-color": b.water } }, ...worldLayers(b)] };
}
async function buildStyle() {
  const b = BASE[theme()];
  let s;
  try {
    const r = await fetch(b.style);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    s = await r.json();
  } catch {
    return { style: fallbackStyle(), fellBack: true };
  }
  s.layers = s.layers.filter((l) => l.source !== "ne2_shaded");  // no shaded relief: the base stays muted
  delete s.sources.ne2_shaded;
  for (const l of s.layers) {
    if (l.type === "background") {
      l.paint = { ...l.paint, "background-color": ["step", ["zoom"], b.water, DETAIL_ZOOM, (l.paint && l.paint["background-color"]) || b.land] };
    } else if (l.source) {
      l.minzoom = Math.max(l.minzoom || 0, DETAIL_ZOOM);
    }
  }
  s.sources.bo_world = worldSource();
  s.layers.splice(s.layers.findIndex((l) => l.type === "background") + 1, 0, ...worldLayers(b, DETAIL_ZOOM));
  return { style: s, fellBack: false };
}

function corridorData(corridors) {
  const features = [];
  for (const c of corridors) {
    const ref = [c.lon, c.lat];
    for (const [lat, lon] of c.landing) {
      if (lat === c.lat && lon === c.lon) continue;
      features.push({ type: "Feature", geometry: { type: "LineString", coordinates: [ref, [lon, lat]] }, properties: { key: c.key, alert: c.alert } });
      features.push({ type: "Feature", geometry: { type: "Point", coordinates: [lon, lat] }, properties: { key: c.key, alert: c.alert, ref: false } });
    }
    features.push({ type: "Feature", geometry: { type: "Point", coordinates: ref }, properties: { key: c.key, alert: c.alert, ref: true } });
  }
  return { type: "FeatureCollection", features };
}

function boundsOf(coords) {
  const b = new ml.LngLatBounds();
  for (const c of coords) b.extend(c);
  return b;
}
// A country's bounds; one that wraps the antimeridian (Russia, Fiji) uses its largest polygon instead.
function countryBounds(f) {
  const [x0, y0, x1, y1] = f.properties.bbox || [];
  if (x0 != null && x1 - x0 <= 180) return [[x0, y0], [x1, y1]];
  let best = null;
  for (const poly of f.geometry.coordinates) if (!best || poly[0].length > best[0].length) best = poly;
  return best ? boundsOf(best[0]) : null;
}

export async function createMap(container, model, { onPick, layers }) {
  const shapes = { type: "FeatureCollection", features: model.shapes.features.map((f) => ({
    type: "Feature", geometry: f.geometry, properties: { key: f.properties.cc.toLowerCase(), cc: f.properties.cc } })) };
  const corr = corridorData(model.corridors);
  const radarCcs = [...new Set(model.radar.features.map((f) => f.properties.key))];
  const shown = { ...layers };
  let selected = null;

  const first = await buildStyle();
  // Open with the whole world exactly one map-width across, so it never repeats side by side, and keep it so when the
  // map changes size (a wider window, a phone turned sideways) until the reader moves the map or picks something.
  const worldZoom = () => Math.max(0, Math.log2(container.clientWidth / 512));
  const WORLD_CENTER = [12, 26];
  let moved = false;
  const map = new ml.Map({
    container, style: first.style, center: WORLD_CENTER, zoom: worldZoom(),
    dragRotate: false, pitchWithRotate: false, cooperativeGestures: true, renderWorldCopies: true,
    attributionControl: { compact: true, customAttribution: "Land: Natural Earth · Cable corridors: schematic, hand-compiled" },
  });
  map.touchZoomRotate.disableRotation();
  map.on("movestart", (e) => { if (e.originalEvent) moved = true; });
  map.on("resize", () => {
    if (!moved && !selected && container.clientWidth > 0) map.jumpTo({ center: WORLD_CENTER, zoom: worldZoom() });
  });
  map.addControl(new ml.NavigationControl({ showCompass: false }), "top-right");
  map.getCanvas().setAttribute("aria-label", "Map. Every item on it is also listed in the Outages, Censorship, Power, Routing and Cables tabs.");
  setFallbackNote(first.fellBack);

  function setFallbackNote(on) {
    const stage = container.parentElement;
    stage.querySelectorAll(".fallback-note").forEach((e) => e.remove());
    if (on) {
      const p = document.createElement("p");
      p.className = "fallback-note";
      p.textContent = "Detailed base map unavailable: showing outline land.";
      stage.append(p);
    }
  }

  const has = (ccs) => ["in", ["get", "cc"], ["literal", ccs]];
  function hitFilter() {
    const ccs = new Set();
    if (shown.outages) model.outageCcs.forEach((c) => ccs.add(c));
    if (shown.ooni) model.ooniCcs.forEach((c) => ccs.add(c));
    if (shown.radar) radarCcs.forEach((k) => ccs.add(k.toUpperCase()));
    return has([...ccs]);
  }

  function addLayers() {
    const k = MARK[theme()];
    map.addSource("bo_countries", { type: "geojson", data: shapes, maxzoom: 8 });
    map.addSource("bo_corridors", { type: "geojson", data: corr, maxzoom: 8 });
    map.addSource("bo_radar", { type: "geojson", data: model.radar, maxzoom: 8 });
    map.addSource("bo_tips", { type: "geojson", data: model.tips, maxzoom: 10 });
    map.addSource("bo_outage_pts", { type: "geojson", data: model.outagePts, maxzoom: 8 });
    map.addSource("bo_power", { type: "geojson", data: model.power, maxzoom: 10 });
    map.addSource("bo_odin", { type: "geojson", data: model.odin, maxzoom: 10 });
    map.addSource("bo_routing", { type: "geojson", data: model.routing, maxzoom: 8 });
    const judged = has(model.judgedCcs);
    map.addLayer({ id: "bo-country-hit", type: "fill", source: "bo_countries", filter: hitFilter(), paint: { "fill-color": "#000000", "fill-opacity": 0 } });
    map.addLayer({ id: "bo-outage-fill", type: "fill", source: "bo_countries", filter: has(model.outageCcs),
      paint: { "fill-color": k.outage, "fill-opacity": ["case", judged, 0.62, 0.36] } });
    map.addLayer({ id: "bo-outage-line", type: "line", source: "bo_countries", filter: has(model.outageCcs),
      paint: { "line-color": k.outageLine, "line-width": ["case", judged, 2, 1.2] } });
    map.addLayer({ id: "bo-ooni-line", type: "line", source: "bo_countries", filter: has(model.ooniCcs),
      paint: { "line-color": k.ooni, "line-width": 2, "line-dasharray": [3, 1.5] } });
    map.addLayer({ id: "bo-sel-line", type: "line", source: "bo_countries", filter: ["==", ["get", "key"], selected || ""],
      paint: { "line-color": k.sel, "line-width": 3 } });
    map.addLayer({ id: "bo-corr-sel", type: "line", source: "bo_corridors", filter: ["all", ["==", ["geometry-type"], "LineString"], ["==", ["get", "key"], selected || ""]],
      paint: { "line-color": k.sel, "line-width": 9, "line-opacity": 0.25 } });
    map.addLayer({ id: "bo-corr-casing", type: "line", source: "bo_corridors", filter: ["==", ["geometry-type"], "LineString"],
      layout: { "line-cap": "round" }, paint: { "line-color": k.casing, "line-width": ["case", ["==", ["get", "alert"], true], 6, 3.4], "line-opacity": 0.55 } });
    map.addLayer({ id: "bo-corr-line", type: "line", source: "bo_corridors", filter: ["all", ["==", ["geometry-type"], "LineString"], ["!=", ["get", "alert"], true]],
      paint: { "line-color": k.corr, "line-width": 1.6, "line-dasharray": [2.5, 1.5] } });
    map.addLayer({ id: "bo-corr-alert", type: "line", source: "bo_corridors", filter: ["all", ["==", ["geometry-type"], "LineString"], ["==", ["get", "alert"], true]],
      layout: { "line-cap": "round" }, paint: { "line-color": k.corrAlert, "line-width": 4 } });
    map.addLayer({ id: "bo-corr-pts", type: "circle", source: "bo_corridors", filter: ["==", ["geometry-type"], "Point"],
      paint: { "circle-radius": ["case", ["==", ["get", "ref"], true], 5, 3], "circle-color": ["case", ["==", ["get", "alert"], true], k.corrAlert, k.corr],
        "circle-stroke-color": k.casing, "circle-stroke-width": 1.2 } });
    // A dot as well as the fill, so a small country's outage (Seychelles, say) shows at world zoom.
    map.addLayer({ id: "bo-outage-pt", type: "circle", source: "bo_outage_pts",
      paint: { "circle-radius": ["case", ["==", ["get", "judged"], true], 6.5, 5], "circle-color": k.outage,
        "circle-stroke-color": k.outageLine, "circle-stroke-width": 1.6 } });
    map.addLayer({ id: "bo-radar", type: "circle", source: "bo_radar",
      paint: { "circle-radius": 6, "circle-color": k.radar, "circle-stroke-color": k.ring, "circle-stroke-width": 1.6 } });
    map.addLayer({ id: "bo-tips", type: "circle", source: "bo_tips",
      paint: { "circle-radius": 5, "circle-color": k.tip, "circle-stroke-color": k.ring, "circle-stroke-width": 1.4 } });
    // ODIN counties: small, larger with more meters out (1,000 -> 3 px, 10,000 -> 5, 100,000 -> 9).
    map.addLayer({ id: "bo-odin", type: "circle", source: "bo_odin",
      paint: { "circle-radius": ["interpolate", ["linear"], ["sqrt", ["get", "meters"]], 0, 1.8, 31.6, 3, 100, 5, 316, 9],
        "circle-color": k.power, "circle-opacity": 0.85, "circle-stroke-color": k.powerLine, "circle-stroke-width": 0.8 } });
    map.addLayer({ id: "bo-routing", type: "circle", source: "bo_routing",
      paint: { "circle-radius": 5, "circle-color": k.routing, "circle-stroke-color": k.ring, "circle-stroke-width": 1.6 } });
    map.addLayer({ id: "bo-power", type: "circle", source: "bo_power",
      paint: { "circle-radius": ["case", ["==", ["get", "routine"], true], 5, 7], "circle-color": k.power,
        "circle-stroke-color": k.powerLine, "circle-stroke-width": 2 } });
    for (const [name, ids] of Object.entries(LAYERS)) for (const id of ids) map.setLayoutProperty(id, "visibility", shown[name] ? "visible" : "none");
  }
  map.on("style.load", addLayers);
  dark.addEventListener("change", async () => {
    const next = await buildStyle();
    setFallbackNote(next.fellBack);
    map.setStyle(next.style, { diff: false });
  });

  // Corridor names as buttons on the map, from LABEL_ZOOM in (at world zoom they would cover the countries with
  // data); they also give keyboard access to each corridor. The Cables tab lists every corridor at any zoom.
  const labels = model.corridors.map((c) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "corr-label" + (c.alert ? " alert" : "");
    b.textContent = `${c.name.split(" / ")[0]} (schematic)`;
    b.setAttribute("aria-label", `${c.name}, drawn schematically${c.alert ? ", alert this run" : ""}: show details`);
    b.addEventListener("click", (e) => { e.stopPropagation(); onPick(c.key); });
    const west = c.key === "eastern-med-levant";  // its label goes over the sea, not the Levant
    return new ml.Marker({ element: b, anchor: west ? "right" : "left", offset: [west ? -9 : 9, 0] }).setLngLat([c.lon, c.lat]).addTo(map);
  });
  const labelState = () => {
    container.classList.toggle("lowzoom", map.getZoom() < LABEL_ZOOM);
    container.classList.toggle("no-corridors", !shown.corridors);
  };
  map.on("zoom", labelState);
  labelState();

  const box = (pt, pad) => [[pt.x - pad, pt.y - pad], [pt.x + pad, pt.y + pad]];
  const live = (ids) => ids.filter((id) => map.getLayer(id) && map.getLayoutProperty(id, "visibility") !== "none");
  function pick(pt) {
    let hits = map.queryRenderedFeatures(box(pt, 7), { layers: live(CLICK_POINTS) });
    if (!hits.length && map.getLayer("bo-country-hit")) hits = map.queryRenderedFeatures(pt, { layers: ["bo-country-hit"] });
    return hits.length ? hits[0].properties.key : null;
  }
  map.on("click", (e) => { const key = pick(e.point); if (key) onPick(key); });
  map.on("mousemove", (e) => { map.getCanvas().style.cursor = pick(e.point) ? "pointer" : ""; });

  return {
    resize: () => map.resize(),
    setLayer(name, on) {
      shown[name] = on;
      for (const id of LAYERS[name] || []) if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", on ? "visible" : "none");
      if (map.getLayer("bo-country-hit")) map.setFilter("bo-country-hit", hitFilter());
      labelState();
    },
    select(key, { fly } = {}) {
      selected = key || null;
      if (map.getLayer("bo-sel-line")) map.setFilter("bo-sel-line", ["==", ["get", "key"], selected || ""]);
      if (map.getLayer("bo-corr-sel")) map.setFilter("bo-corr-sel", ["all", ["==", ["geometry-type"], "LineString"], ["==", ["get", "key"], selected || ""]]);
      labels.forEach((m, i) => m.getElement().classList.toggle("selected", model.corridors[i].key === selected));
      if (!fly || !selected) return;
      moved = true;
      let b = null;
      const c = model.corridors.find((x) => x.key === selected);
      if (c) b = boundsOf([[c.lon, c.lat], ...c.landing.map(([lat, lon]) => [lon, lat])]);
      const f = !c && model.shapes.features.find((x) => x.properties.cc.toLowerCase() === selected);
      if (f) b = countryBounds(f);
      const t = !c && !f && model.tips.features.find((x) => x.properties.key === selected);
      if (t) { map.flyTo({ center: t.geometry.coordinates, zoom: 5 }); return; }
      const pts = !c && !f ? [model.power, model.routing, model.odin].flatMap((fc) => fc.features.filter((x) => x.properties.key === selected)) : [];
      if (pts.length === 1) { map.flyTo({ center: pts[0].geometry.coordinates, zoom: 5 }); return; }
      if (pts.length > 1) b = boundsOf(pts.map((x) => x.geometry.coordinates));
      if (!b) return;
      const wide = container.clientWidth >= 900;
      map.fitBounds(b, { padding: { top: 40, bottom: 40, left: 40, right: wide ? 400 : 40 }, maxZoom: 5, duration: 900 });
    },
  };
}
