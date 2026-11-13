/* Blackout UI — renders snapshot.json and feed.geojson (tic-feed/2) */
(function () {
  "use strict";

  var T = window.TicTime;

  function when(iso) { return iso ? T.format(iso) : "—"; }

  // Outages that meet a published indicator; 20-25% drops are kept in the snapshot for corridor correlation only.
  function published(o) { return Boolean(o.indicator_id) && o.lat != null && o.lon != null; }

  function pct(v) { return v == null ? "not measured" : (v > 0 ? "-" + v + "%" : "no drop"); }

  var STATE = {
    snap: null,
    feed: null,
    filter: "all",
    query: "",
    selectedId: "",
    layers: { corridors: true, outages: true, ooni: true, tips: true }
  };

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function project(lat, lon) {
    var x = ((Number(lon) + 180) / 360) * 960;
    var y = ((90 - Number(lat)) / 180) * 440;
    return [Math.round(x * 10) / 10, Math.round(y * 10) / 10];
  }

  function matchesQuery(text) {
    if (!STATE.query) return true;
    return String(text || "").toLowerCase().indexOf(STATE.query.toLowerCase()) !== -1;
  }

  function matchesHash(itemKeys) {
    if (!STATE.selectedId) return true;
    var target = STATE.selectedId.toLowerCase();
    for (var i = 0; i < itemKeys.length; i++) {
      if (String(itemKeys[i] || "").toLowerCase() === target) return true;
    }
    return false;
  }

  function renderMap() {
    var svg = document.getElementById("world-map");
    if (!svg || !STATE.snap) return;
    var snap = STATE.snap;
    var parts = [];

    // Graticule grid
    for (var lon = -120; lon <= 120; lon += 60) {
      var gx = project(0, lon)[0];
      parts.push('<line class="map-grid" x1="' + gx + '" y1="0" x2="' + gx + '" y2="440"/>');
    }
    for (var lat = -60; lat <= 60; lat += 30) {
      var gy = project(lat, 0)[1];
      parts.push('<line class="map-grid" x1="0" y1="' + gy + '" x2="960" y2="' + gy + '"/>');
    }

    // Simplified continental backdrop polygons (equirectangular 960x440)
    var continents = [
      "M 75,65 L 245,65 L 285,175 L 230,220 L 155,185 L 95,125 Z", // N America
      "M 235,230 L 325,255 L 305,385 L 255,395 L 225,295 Z", // S America
      "M 435,70 L 585,65 L 595,155 L 445,165 Z", // Europe
      "M 435,175 L 590,175 L 615,245 L 565,355 L 495,355 L 430,245 Z", // Africa
      "M 585,65 L 845,75 L 860,220 L 725,255 L 600,205 Z", // Asia
      "M 745,295 L 855,295 L 860,365 L 745,365 Z" // Australia
    ];
    for (var c = 0; c < continents.length; c++) {
      parts.push('<path class="map-land" d="' + continents[c] + '"/>');
    }

    var alertByCorrId = {};
    (snap.corridor_alerts || []).forEach(function (ca) {
      alertByCorrId[ca.corridor_id] = ca;
    });

    // 1. Submarine Cable Corridors
    if (STATE.layers.corridors) {
      (snap.corridors || []).forEach(function (corr) {
        var pt = project(corr.lat, corr.lon);
        var isAlert = Boolean(alertByCorrId[corr.id]);
        var cls = "map-corridor-ring" + (isAlert ? " active" : "");
        parts.push(
          '<g class="map-marker" data-select="' + esc(corr.id) + '">' +
          '<circle class="' + cls + '" cx="' + pt[0] + '" cy="' + pt[1] + '" r="' + (isAlert ? 18 : 13) + '"/>' +
          '<circle fill="' + (isAlert ? "#f0b72f" : "#58a6ff") + '" cx="' + pt[0] + '" cy="' + pt[1] + '" r="3.5"/>' +
          '<text class="map-label" x="' + (pt[0] + 8) + '" y="' + (pt[1] - 6) + '">' + esc(corr.id.split("-")[0].toUpperCase()) + '</text>' +
          '</g>'
        );
        (corr.landing_coords || []).forEach(function (lc) {
          var lpt = project(lc[0], lc[1]);
          parts.push('<circle class="map-landing" cx="' + lpt[0] + '" cy="' + lpt[1] + '" r="2"/>');
        });
      });
    }

    // 2. OONI anomaly rates meeting BO-IND-3 (same filter as the list and the tile)
    if (STATE.layers.ooni) {
      (snap.censorship || []).filter(function (c) {
        return c.anomaly_rate >= 0.15 && c.measurement_count >= 50;
      }).forEach(function (c) {
        var pt = project(c.lat, c.lon);
        parts.push(
          '<g class="map-marker" data-select="' + esc(c.country) + '">' +
          '<circle fill="rgba(188, 140, 255, 0.35)" stroke="#bc8cff" stroke-width="1.5" cx="' + (pt[0] + 5) + '" cy="' + (pt[1] - 4) + '" r="8"/>' +
          '<text class="map-label" x="' + (pt[0] + 15) + '" y="' + (pt[1] - 2) + '">' + esc(c.country) + ' (OONI)</text>' +
          '</g>'
        );
      });
    }

    // 3. IODA Outages
    if (STATE.layers.outages) {
      (snap.outages || []).filter(published).forEach(function (o) {
        var pt = project(o.lat, o.lon);
        var r = Math.max(6, Math.min(14, Math.round(o.drop_pct / 5)));
        parts.push(
          '<g class="map-marker" data-select="' + esc(o.country) + '">' +
          '<circle fill="rgba(248, 81, 73, 0.45)" stroke="#f85149" stroke-width="1.8" cx="' + pt[0] + '" cy="' + pt[1] + '" r="' + r + '"/>' +
          '<text class="map-label" x="' + (pt[0] + r + 3) + '" y="' + (pt[1] + 3) + '">' + esc(o.country) + ' -' + esc(o.drop_pct) + '%</text>' +
          '</g>'
        );
      });
    }

    // 4. Cross-Program Tips
    if (STATE.layers.tips) {
      (snap.tips || []).forEach(function (t) {
        if (t.lat == null || t.lon == null) return;
        var pt = project(t.lat, t.lon);
        parts.push(
          '<g class="map-marker" data-select="' + esc(t.item_id || t.program) + '">' +
          '<polygon fill="#39d353" stroke="#0b0e14" stroke-width="1" points="' +
          pt[0] + ',' + (pt[1] - 7) + ' ' + (pt[0] + 6) + ',' + (pt[1] + 5) + ' ' + (pt[0] - 6) + ',' + (pt[1] + 5) + '"/>' +
          '</g>'
        );
      });
    }

    svg.innerHTML = parts.join("");
  }

  function renderMapDetail() {
    var detail = document.getElementById("map-detail");
    var clearBtn = document.getElementById("clear-filter");
    if (!detail || !STATE.snap) return;

    if (!STATE.selectedId && STATE.filter === "all" && !STATE.query) {
      detail.className = "map-detail hidden";
      if (clearBtn) clearBtn.className = "btn-clear hidden";
      return;
    }
    if (clearBtn) clearBtn.className = "btn-clear";

    if (!STATE.selectedId) {
      detail.className = "map-detail hidden";
      return;
    }

    var target = STATE.selectedId.toLowerCase();
    var snap = STATE.snap;
    var foundTitle = "Selected Target: " + STATE.selectedId.toUpperCase();
    var foundDesc = "Showing filtered telemetry matching #" + STATE.selectedId + ".";

    (snap.corridors || []).forEach(function (c) {
      if (c.id.toLowerCase() === target) {
        foundTitle = c.name + " (" + c.id + ")";
        foundDesc = "Cables: " + (c.cables || []).join(", ") + " · Landing stations: " + (c.landing_stations || []).join(" · ");
      }
    });
    (snap.outages || []).filter(published).forEach(function (o) {
      if (o.country.toLowerCase() === target || o.id.toLowerCase() === target) {
        foundTitle = o.country_name + " (" + o.country + ") — -" + o.drop_pct + "% Connectivity Drop";
        foundDesc = o.summary + " (Indicator: " + o.indicator_id +
          (o.baseline_zscore != null ? ", z-score " + o.baseline_zscore + " against the 7 days before" : "") + ")";
      }
    });
    (snap.censorship || []).forEach(function (c) {
      if (c.country.toLowerCase() === target && foundDesc.indexOf("Showing filtered") === 0) {
        foundTitle = c.country_name + " (" + c.country + ") — OONI anomaly rate";
        foundDesc = c.summary;
      }
    });

    detail.className = "map-detail";
    detail.innerHTML =
      '<div><strong>' + esc(foundTitle) + '</strong><div class="small muted">' + esc(foundDesc) + '</div></div>' +
      '<button type="button" class="btn-clear" id="detail-close">Show All</button>';
    var closeBtn = document.getElementById("detail-close");
    if (closeBtn) {
      closeBtn.onclick = clearSelection;
    }
  }

  function link(url, text) {
    return /^https:\/\//.test(url || "") ? '<a href="' + esc(url) + '" rel="noopener">' + esc(text) + '</a>' : "";
  }

  // Cloudflare Radar (BO-IND-9, BO-IND-10): annotated outages with Cloudflare's stated cause, and verified anomalies.
  function renderRadar(snap) {
    var el = document.getElementById("radar-list");
    if (!el) return;
    var r = snap.radar;
    if (!r) {
      el.innerHTML = '<p class="muted small">' + (snap.mode === "live" ? "Cloudflare Radar was not read this run." :
        "No Radar data in a seed build.") + '</p>';
      return;
    }
    function human(s) { return s ? String(s).replace(/_/g, " ").toLowerCase() : ""; }
    function span(a) { return esc(when(a.start)) + (a.end ? ' to ' + esc(when(a.end)) : ', ongoing'); }
    var show = STATE.filter === "all";
    var rows = [];
    (r.annotations || []).forEach(function (a) {
      var hay = [a.description, a.cause, a.type, (a.countries || []).join(" "), (a.regions || []).join(" "),
        "BO-IND-9"].join(" ");
      if (!(show && matchesQuery(hay) && matchesHash(["radar"].concat(a.countries || [])))) return;
      rows.push('<div class="row"><div class="row-title"><strong>' +
          esc((a.regions && a.regions[0]) || (a.countries || []).join(", ") || "Location not given") + ': ' +
          esc(human(a.cause) || "cause not stated") + '</strong><span>' +
          (a.type ? '<span class="badge">' + esc(human(a.type)) + '</span> ' : '') +
          '<span class="badge badge-doctrine">BO-IND-9</span></span></div>' +
        '<div class="small">' + esc(a.description) + '</div>' +
        '<div class="small muted">' + span(a) +
          ((a.asns || []).length ? ' &middot; AS' + esc(a.asns.join(", AS")) : '') + '</div>' +
        '<div class="small">' + link(a.link, "Cloudflare's post") + ' <span class="muted">&middot; Cause as Cloudflare ' +
          'states it; Blackout does not assess it.</span></div></div>');
    });
    (r.anomalies || []).forEach(function (a) {
      var hay = [a.country, a.asn ? "AS" + a.asn : "", a.asn_name, a.status, "BO-IND-10"].join(" ");
      if (!(show && matchesQuery(hay) && matchesHash(["radar", a.country, a.asn ? "AS" + a.asn : ""]))) return;
      rows.push('<div class="row"><div class="row-title"><strong>' + esc(a.country || "Location not given") +
          (a.asn ? ', AS' + esc(a.asn) + (a.asn_name ? ' (' + esc(a.asn_name) + ')' : '') : '') +
          ': traffic anomaly</strong><span><span class="badge">verified (' + esc(a.status) + ')</span> ' +
          '<span class="badge badge-doctrine">BO-IND-10</span></span></div>' +
        '<div class="small muted">' + span(a) +
          ((a.seen_in || []).length ? ' &middot; seen in Radar ' + esc(a.seen_in.join(", ")) + ' data' : '') + '</div>' +
        '<div class="small muted">Radar gives no cause for an anomaly; Blackout does not assess one.</div></div>');
    });
    var read = r.read || {};
    var unread = ["annotations", "anomalies"].filter(function (k) { return read[k] === false; });
    el.innerHTML = (rows.length ? rows.join("") : '<p class="muted small">' + (unread.length === 2 ?
        "Cloudflare Radar could not be read this run." :
        "No Radar outage or verified anomaly in the last " + esc(r.recent_hours || 72) + " h matches.") + '</p>') +
      (unread.length === 1 ? '<p class="muted small">Not read this run: Radar ' + esc(unread[0]) + '.</p>' : '');
  }

  // Provider watch (BO-WP-3): watched networks on IODA, provider status feeds, open incidents, concurrent onsets.
  function renderProviders(snap) {
    var live = snap.mode === "live";
    var show = STATE.filter === "all" || STATE.filter === "providers";
    var watch = snap.asn_watch || [];
    var byAsn = {};
    watch.forEach(function (w) { byAsn[w.asn] = w; });
    var incidents = snap.provider_incidents || [];
    var feeds = snap.provider_feeds || [];

    var asnEl = document.getElementById("asn-watch");
    if (asnEl) {
      var rows = (snap.asn_watchlist || []).filter(function (w) {
        var r = byAsn[w.asn] || {};
        var hay = [w.asn, w.label, w.provider, r.asn_name, r.summary, r.indicator_id, "BO-IND-6"].join(" ");
        return show && matchesQuery(hay) && matchesHash([w.asn, w.provider, "providers"]);
      });
      asnEl.innerHTML = rows.length ? rows.map(function (w) {
        var r = byAsn[w.asn];
        if (!r) {
          return '<div class="row" id="' + esc(w.asn) + '"><div class="row-title"><strong>' + esc(w.label) +
            ' (' + esc(w.asn) + ')</strong><span class="badge">' + (live ? "not read this run" : "no reading (seed build)") +
            '</span></div>' + (live ? '<div class="small muted">IODA could not be read for this network; see the ' +
            'problems in the snapshot JSON. Nothing is shown in its place.</div>' : '') + '</div>';
        }
        var hit = r.indicator_id === "BO-IND-6";
        return '<div class="row" id="' + esc(r.asn) + '">' +
          '<div class="row-title"><strong>' + esc(r.label) + ' (' + esc(r.asn) + ')</strong><span>' +
            '<span class="badge ' + (hit ? 'badge-danger' : '') + '">' +
              (r.drop_pct == null ? "not measured" : (r.drop_pct > 0 ? "-" + esc(r.drop_pct) + "%" : "no drop")) + '</span> ' +
            (hit ? '<span class="badge badge-doctrine">BO-IND-6</span> ' : '') +
            (r.ioda_events ? '<span class="badge badge-warn">' + esc(r.ioda_events) + ' IODA event(s)</span>' : '') +
          '</span></div>' +
          '<div class="small muted">' + esc(r.asn_name || "") + ' · BGP ' + esc(pct(r.bgp_drop_pct)) +
            (r.bgp_zscore != null ? ' (z ' + esc(r.bgp_zscore) + ')' : '') + ' · probing ' + esc(pct(r.probing_drop_pct)) +
            (r.probing_zscore != null ? ' (z ' + esc(r.probing_zscore) + ')' : '') + '</div>' +
          '<div class="small muted">Window ' + esc(when(r.window_start)) + ' to ' + esc(when(r.window_end)) +
            ' (' + esc(r.window_basis) + ')</div>' +
        '</div>';
      }).join("") : '<p class="muted small">No matching watched network.</p>';
    }

    var feedEl = document.getElementById("provider-feeds");
    if (feedEl) {
      var fr = feeds.filter(function (f) {
        var hay = [f.label, f.publisher, f.provider, f.note, "BO-IND-7"].join(" ");
        return show && matchesQuery(hay) && matchesHash(["provider-" + f.id, f.provider, "providers"]);
      });
      feedEl.innerHTML = fr.length ? fr.map(function (f) {
        var badge = !f.read ? '<span class="badge badge-danger">not read this run</span>' :
          (f.open ? '<span class="badge badge-warn">' + esc(f.open) + ' open</span>' : '<span class="badge">none listed</span>');
        return '<div class="row" id="provider-' + esc(f.id) + '">' +
          '<div class="row-title"><strong>' + esc(f.label) + '</strong><span>' + badge +
            ' <span class="badge">grade ' + esc(f.grade) + '</span></span></div>' +
          '<div class="small muted">' + esc(f.publisher) + ' · ' + link(f.page, "status page") + '</div>' +
          (f.note ? '<div class="small muted">' + esc(f.note) + '</div>' : '') +
        '</div>';
      }).join("") : '<p class="muted small">' + (feeds.length ? "No matching status feed." :
        (live ? "No status feed was read this run." : "No status feeds are read in a seed build.")) + '</p>';
    }

    var incEl = document.getElementById("provider-incidents");
    if (incEl) {
      var ir = incidents.filter(function (i) {
        var hay = [i.provider_label, i.title, i.status, i.service, (i.locations || []).join(" "),
          (i.affected || []).join(" "), "BO-IND-7"].join(" ");
        return show && matchesQuery(hay) && matchesHash(["provider-" + i.feed, i.provider, "providers"]);
      });
      incEl.innerHTML = ir.length ? ir.map(function (i) {
        var where = (i.locations && i.locations.length) ? i.locations : (i.affected || []);
        return '<div class="row">' +
          '<div class="row-title"><strong>' + esc(i.provider_label) + ': ' + esc(i.title) +
            ((i.locations || []).length ? ' (' + esc(i.locations.slice(0, 3).join(", ")) +
              (i.locations.length > 3 ? ' and ' + (i.locations.length - 3) + ' more' : '') + ')' : '') + '</strong><span>' +
            (i.status ? '<span class="badge badge-warn">' + esc(i.status) + '</span> ' : '') +
            (i.severity ? '<span class="badge">' + esc(i.severity) + '</span> ' : '') +
            '<span class="badge badge-doctrine">BO-IND-7</span></span></div>' +
          '<div class="small muted">' + (i.started_at ? 'Since ' + esc(when(i.started_at)) : 'Start not given') +
            (i.updated_at ? ' · last update ' + esc(when(i.updated_at)) : '') +
            (i.updates ? ' · ' + esc(i.updates) + ' update(s)' : '') +
            (i.service ? ' · ' + esc(i.service) : '') + '</div>' +
          (where.length ? '<div class="small muted">Affected, as the provider lists them: ' + esc(where.slice(0, 12).join(", ")) +
            (where.length > 12 ? ' and ' + (where.length - 12) + ' more' : '') + '</div>' : '') +
          '<div class="small">' + link(i.link, "Provider's page") + ' <span class="muted">· Reported by the provider; ' +
            'Blackout does not assess its cause.</span></div>' +
        '</div>';
      }).join("") : '<p class="muted small">' + (feeds.some(function (f) { return f.read; }) ?
        "None listed as open on the feeds that were read." : "No status feed was read, so nothing can be said.") + '</p>';
    }

    var concEl = document.getElementById("provider-concurrency");
    if (concEl) {
      var c = snap.provider_concurrency;
      concEl.innerHTML = (c && show) ? '<div class="row warn"><div class="row-title"><strong>Outages at ' +
        esc(c.providers.length) + ' providers began within 2 h: ' + esc(c.provider_labels.join(", ")) + '</strong>' +
        '<span class="badge badge-doctrine">BO-IND-8</span></div><div class="small">' +
        esc(c.onsets.map(function (o) { return o.label + " from " + when(o.onset); }).join("; ")) +
        '. Onsets this close do not by themselves show a shared cause or a cascade.' +
        (c.caution ? ' ' + esc(c.caution) : '') + '</div></div>' : '';
    }
  }

  function render() {
    if (!STATE.snap) return;
    var snap = STATE.snap;
    var outages = (snap.outages || []).filter(published);
    var ooni = (snap.censorship || []).filter(function (c) { return c.anomaly_rate >= 0.15 && c.measurement_count >= 50; });
    var corr = snap.corridor_alerts || [];
    var allCorridors = snap.corridors || [];
    var tips = snap.tips || [];
    var features = (STATE.feed && STATE.feed.features) ? STATE.feed.features : [];

    // Test-data or collection-problem banner
    var banner = document.getElementById("mode-banner");
    if (banner) {
      var problems = (snap.collection && snap.collection.problems) || [];
      if (snap.mode !== "live") {
        banner.className = "mode-banner";
        banner.innerHTML = "<strong>Test data.</strong> Everything on this page comes from made-up seed records used to " +
          "check the pipeline (feed status <code>test</code>). None of it is an observation.";
      } else if (problems.length) {
        var srcs = {};
        problems.forEach(function (p) { srcs[String(p.source || "unknown").split(":")[0]] = true; });
        banner.className = "mode-banner problems";
        banner.innerHTML = "<strong>Incomplete collection.</strong> " + problems.length + " problem(s) reading " +
          esc(Object.keys(srcs).join(", ")) + "; missing data is left out, not filled in. Details in the snapshot JSON.";
      } else {
        banner.className = "mode-banner hidden";
        banner.innerHTML = "";
      }
    }

    // Summary tiles
    var tiles = document.getElementById("tiles");
    if (tiles) {
      tiles.innerHTML = [
        '<div class="tile"><span class="k">Outages (BO-IND-1)</span><span class="v">' +
          outages.filter(function (o) { return o.indicator_id === "BO-IND-1"; }).length + "</span></div>",
        '<div class="tile"><span class="k">Cable Corridor Alerts</span><span class="v">' + corr.length + "</span></div>",
        '<div class="tile"><span class="k">OONI anomaly &ge;15%</span><span class="v">' + ooni.length + "</span></div>",
        '<div class="tile"><span class="k">Suite Tips</span><span class="v">' + tips.length + "</span></div>",
        '<div class="tile"><span class="k">Provider incidents open</span><span class="v">' +
          (snap.provider_incidents || []).length + "</span></div>",
        '<div class="tile"><span class="k">Watched networks dropping</span><span class="v">' +
          (snap.asn_watch || []).filter(function (w) { return w.indicator_id === "BO-IND-6"; }).length + "</span></div>",
        '<div class="tile"><span class="k">As Of</span><span class="v">' + esc(when(snap.generated_at)) + "</span></div>"
      ].join("");
    }

    renderMap();
    renderMapDetail();

    // Filter corridors
    var filteredCorr = corr.filter(function (c) {
      if (STATE.filter === "ooni" || STATE.filter === "providers") return false;
      if (STATE.filter === "tips" && !(c.tips && c.tips.length)) return false;
      var hay = [c.name, c.corridor_id, c.summary, (c.countries || []).join(" "), (c.cables || []).join(" "), (c.indicator_ids || []).join(" ")].join(" ");
      var keys = [c.corridor_id, c.id].concat(c.countries || []);
      return matchesQuery(hay) && matchesHash(keys);
    });

    var corrEl = document.getElementById("corridors");
    if (corrEl) {
      corrEl.innerHTML = filteredCorr.length ? filteredCorr.map(function (c) {
        var indBadges = (c.indicator_ids || ["BO-IND-2"]).map(function (id) {
          return '<span class="badge badge-doctrine">' + esc(id) + '</span>';
        }).join(" ");
        var tipBadges = (c.tips || []).map(function (t) {
          return '<span class="badge badge-tip">Tip: ' + esc(t.program.toUpperCase()) + ' (' + esc(t.distance_km) + ' km)</span>';
        }).join(" ");
        return '<div class="row warn" data-select="' + esc(c.corridor_id) + '">' +
          '<div class="row-title"><strong><a href="#' + esc(c.corridor_id) + '" style="color:inherit;text-decoration:none">' + esc(c.name) + '</a></strong> ' +
          '<span><span class="badge badge-warn">Mean -' + esc(c.mean_drop_pct) + '%</span> ' + indBadges + ' ' + tipBadges + '</span></div>' +
          '<div class="small">' + esc(c.summary) + '</div>' +
          '<div class="small muted">Cables: ' + esc((c.cables || []).join(", ")) + ' &middot; Landing stations: ' + esc((c.landing_stations || []).join(" · ")) + '</div>' +
        '</div>';
      }).join("") : '<p class="muted small">No matching submarine cable corridor alerts.</p>';
    }

    // All 5 corridors status matrix
    var matrixEl = document.getElementById("corridor-matrix");
    if (matrixEl) {
      var alertIds = {};
      corr.forEach(function (ca) { alertIds[ca.corridor_id] = ca; });
      matrixEl.innerHTML = allCorridors.map(function (mc) {
        var activeAlert = alertIds[mc.id];
        return '<div class="corr-box' + (activeAlert ? ' corr-alert' : '') + '">' +
          '<div><strong><a href="#' + esc(mc.id) + '" style="color:inherit;text-decoration:none">' + esc(mc.name) + '</a></strong></div>' +
          '<div class="badge-row"><span class="badge ' + (activeAlert ? 'badge-warn' : '') + '">' +
            (activeAlert ? ('WARNING (-' + esc(activeAlert.mean_drop_pct) + '%)') : 'Nominal') +
          '</span><span class="badge">' + esc((mc.countries || []).join(", ")) + '</span></div>' +
          '<div class="muted" style="margin-top:6px">Cables: ' + esc((mc.cables || []).join(", ")) + '</div>' +
        '</div>';
      }).join("");
    }

    // Filter outages
    var filteredOutages = outages.filter(function (o) {
      if (STATE.filter === "corridors" || STATE.filter === "ooni" || STATE.filter === "providers") return false;
      if (STATE.filter === "corroborated" && !o.corroborated) return false;
      if (STATE.filter === "tips" && !(o.related_tips && o.related_tips.length)) return false;
      var hay = [o.country, o.country_name, o.asn, o.asn_name, o.datasource, o.indicator_id, o.summary].join(" ");
      return matchesQuery(hay) && matchesHash([o.country, o.id]);
    });

    var outEl = document.getElementById("outages");
    if (outEl) {
      outEl.innerHTML = filteredOutages.length ? filteredOutages.map(function (o) {
        var z = o.baseline_zscore != null ? 'z=' + o.baseline_zscore : '';
        var artefact = o.indicator_id === "BO-IND-4";
        var related = (o.related_tips || []).map(function (t) {
          return esc((t.program || "suite").toUpperCase()) + ": " + esc(t.title || t.item_id);
        }).join(" · ");
        return '<div class="row" id="outage-' + esc(o.country) + '">' +
          '<div class="row-title">' +
            '<strong><a href="#' + esc(o.country) + '" style="color:inherit;text-decoration:none">' + esc(o.country_name) + ' (' + esc(o.country) + ')</a></strong> ' +
            '<span>' +
              '<span class="badge ' + (artefact ? '' : 'badge-danger') + '">-' + esc(o.drop_pct) + '%</span> ' +
              '<span class="badge badge-doctrine">' + esc(o.indicator_id) + (artefact ? ' · single-source dip' : '') + '</span> ' +
              (o.corroborated ? '<span class="badge badge-tip">BGP + probing</span> ' : '') +
              (z ? '<span class="badge">' + esc(z) + '</span>' : '') +
            '</span>' +
          '</div>' +
          '<div class="small">' + (o.asn ? '<code>' + esc(o.asn) + '</code> — ' + esc(o.asn_name) + ': ' : '') + esc(o.summary) + '</div>' +
          '<div class="small muted">From ' + esc(when(o.start_time)) + ' for ' + esc(o.duration_minutes) + ' min · BGP ' +
            esc(pct(o.bgp_drop_pct)) + ' · probing ' + esc(pct(o.probing_drop_pct)) + ' · darknet ' + esc(pct(o.darknet_drop_pct)) +
            (o.ioda_score != null ? ' · IODA score ' + esc(Math.round(o.ioda_score)) : '') + '</div>' +
          (related ? '<div class="small muted">Context (does not change the assessment): ' + related + '</div>' : '') +
        '</div>';
      }).join("") : '<p class="muted small">No matching IODA outages.</p>';
    }

    // Filter OONI
    var filteredOoni = ooni.filter(function (c) {
      if (STATE.filter === "corridors" || STATE.filter === "corroborated" || STATE.filter === "tips" ||
          STATE.filter === "providers") return false;
      var hay = [c.country, c.country_name, c.test_name, c.summary, "BO-IND-3"].join(" ");
      return matchesQuery(hay) && matchesHash([c.country, c.id]);
    });

    var ooniEl = document.getElementById("censorship");
    if (ooniEl) {
      ooniEl.innerHTML = filteredOoni.length ? filteredOoni.map(function (c) {
        return '<div class="row" id="ooni-' + esc(c.country) + '">' +
          '<div class="row-title">' +
            '<strong><a href="#' + esc(c.country) + '" style="color:inherit;text-decoration:none">' + esc(c.country_name) + ' (' + esc(c.country) + ')</a></strong> ' +
            '<span><span class="badge badge-ooni">' + (c.anomaly_rate * 100).toFixed(1) + '% anomaly</span> <span class="badge badge-doctrine">BO-IND-3</span></span>' +
          '</div>' +
          '<div class="small">' + esc(c.summary) + '</div>' +
          '<div class="small muted">Confirmed blocking: ' + (c.confirmed_rate * 100).toFixed(1) + '% &middot; Measurements: ' + esc(c.measurement_count) + ' &middot; OK: ' + esc(c.ok_count != null ? c.ok_count : "not reported") + '</div>' +
        '</div>';
      }).join("") : '<p class="muted small">No country at or above BO-IND-3 matches.</p>';
    }

    // Cross-program tips list
    var filteredTips = tips.filter(function (t) {
      if (STATE.filter === "ooni" || STATE.filter === "providers") return false;
      var hay = [t.program, t.title, t.interest_id, t.indicator_id, (t.countries || []).join(" ")].join(" ");
      return matchesQuery(hay) && matchesHash([t.item_id, t.program].concat(t.countries || []));
    });

    var tipsEl = document.getElementById("tips-list");
    if (tipsEl) {
      tipsEl.innerHTML = filteredTips.length ? filteredTips.map(function (t) {
        return '<div class="row">' +
          '<div class="row-title">' +
            '<strong>' + esc((t.program || "suite").toUpperCase()) + ': ' + esc(t.title) + '</strong>' +
            '<span><span class="badge badge-tip">' + esc(t.interest_id || "BO-INT") + '</span> <span class="badge badge-doctrine">' + esc(t.indicator_id || "BO-IND-5") + '</span></span>' +
          '</div>' +
          '<div class="small muted">Countries: ' + esc((t.countries || []).join(", ") || "none given") +
            ' &middot; Tier: ' + esc(t.tier || "not given") +
            (t.lat != null && t.lon != null ? ' &middot; Location: ' + esc(t.lat) + ', ' + esc(t.lon) : ' &middot; No location (cannot meet BO-IND-5)') +
            (t.published_at ? ' &middot; Published ' + esc(when(t.published_at)) : '') +
            (/^https?:\/\//.test(t.link || "") ? ' &middot; <a href="' + esc(t.link) + '" rel="noopener">Source item</a>' : '') + '</div>' +
        '</div>';
      }).join("") : '<p class="muted small">No active cross-program tips matching filter.</p>';
    }

    renderProviders(snap);
    renderRadar(snap);

    // Contract-v2 Doctrine Assessments
    var assessEl = document.getElementById("assessments");
    if (assessEl) {
      var filteredFeat = features.filter(function (f) {
        var p = f.properties || {};
        var hay = [p.id, p.title, p.summary, p.warning_problem_id, p.hypothesis_id, p.indicator_id, (p.countries || []).join(" "), (p.ncf || []).join(" ")].join(" ");
        var keys = [p.id].concat(p.countries || []);
        if (p.id && p.id.indexOf("corridor:") !== -1) {
          keys.push(p.id.split(":")[2]);
        }
        if (p.visual && p.visual.url && p.visual.url.indexOf("#") !== -1) keys.push(p.visual.url.split("#")[1]);
        if (p.warning_problem_id === "BO-WP-3") keys.push("providers");
        if (STATE.filter === "providers" && p.warning_problem_id !== "BO-WP-3") return false;
        return matchesQuery(hay) && matchesHash(keys);
      });

      assessEl.innerHTML = filteredFeat.length ? filteredFeat.map(function (f) {
        var p = f.properties || {};
        var alts = (p.alternatives || []).map(function (a) {
          return '<li><strong>' + esc(a.hypothesis) + '</strong> — <em>Tell apart:</em> ' + esc(a.tell_apart) + '</li>';
        }).join("");
        var srcs = (p.sources || []).map(function (s) {
          return '<li><strong>' + esc(s.publisher) + '</strong> (' + esc(s.source_type || "telemetry") + '): ' + esc(s.descriptor || s.title) + '</li>';
        }).join("");
        return '<div class="row">' +
          '<div class="row-title">' +
            '<strong>' + esc(p.title) + '</strong>' +
            '<span>' +
              '<span class="badge ' + (p.tier === "warning" ? "badge-warn" : "badge-ooni") + '">' + esc((p.tier || "").toUpperCase()) + ' &middot; ' + esc(p.kind) + '</span> ' +
              (p.confidence ? '<span class="badge badge-doctrine">Confidence: ' + esc(p.confidence) + '</span> ' : '') +
              (p.likelihood_term ? '<span class="badge badge-doctrine">Likelihood: ' + esc(p.likelihood_term) + ' (' + Math.round((p.probability || 0) * 100) + '%)</span>' : '') +
            '</span>' +
          '</div>' +
          '<div class="small">' + esc(p.summary) + '</div>' +
          '<div class="badge-row">' +
            (p.warning_problem_id ? '<span class="badge">' + esc(p.warning_problem_id) + '</span>' : '') +
            (p.hypothesis_id ? '<span class="badge">Hypothesis: ' + esc(p.hypothesis_id) + '</span>' : '') +
            (p.indicator_id ? '<span class="badge">Indicator: ' + esc(p.indicator_id) + '</span>' : '') +
            ((p.ncf || []).map(function (n) { return '<span class="badge">NCF: ' + esc(n) + '</span>'; }).join("")) +
          '</div>' +
          (p.confidence_basis ? '<div class="small muted" style="margin-top:4px">Rubric: ' + esc(p.confidence_basis) + '</div>' : '') +
          (alts ? '<details class="alt-details"><summary>Competing Hypotheses &amp; Diagnostic Indicators (' + (p.alternatives || []).length + ')</summary><ul class="alt-list">' + alts + '</ul></details>' : '') +
          (srcs ? '<details class="alt-details"><summary>Source Lineage &amp; Corroboration (' + (p.sources || []).length + ')</summary><ul class="alt-list">' + srcs + '</ul></details>' : '') +
        '</div>';
      }).join("") : '<p class="muted small">No contract-v2 features matching filter.</p>';
    }
  }

  function clearSelection() {
    STATE.selectedId = "";
    STATE.filter = "all";
    STATE.query = "";
    var searchInput = document.getElementById("search-input");
    if (searchInput) searchInput.value = "";
    var chips = document.querySelectorAll("#filter-chips .chip");
    for (var i = 0; i < chips.length; i++) {
      chips[i].classList.toggle("active", chips[i].getAttribute("data-filter") === "all");
    }
    if (window.history && window.history.replaceState) {
      window.history.replaceState(null, "", window.location.pathname);
    }
    render();
  }

  function syncFromHash() {
    var h = (window.location.hash || "").replace(/^#/, "").trim();
    if (h && h !== "assessments-h" && h !== "providers") {
      STATE.selectedId = h;
    } else {
      STATE.selectedId = "";
    }
    render();
  }

  function bindEvents() {
    var chips = document.getElementById("filter-chips");
    if (chips) {
      chips.addEventListener("click", function (e) {
        var btn = e.target.closest(".chip");
        if (!btn) return;
        STATE.filter = btn.getAttribute("data-filter") || "all";
        var all = chips.querySelectorAll(".chip");
        for (var i = 0; i < all.length; i++) {
          all[i].classList.toggle("active", all[i] === btn);
        }
        render();
      });
    }

    var searchInput = document.getElementById("search-input");
    if (searchInput) {
      searchInput.addEventListener("input", function () {
        STATE.query = searchInput.value.trim();
        render();
      });
    }

    var clearBtn = document.getElementById("clear-filter");
    if (clearBtn) {
      clearBtn.addEventListener("click", clearSelection);
    }

    var layersEl = document.getElementById("map-layers");
    if (layersEl) {
      layersEl.addEventListener("change", function (e) {
        var cb = e.target;
        if (cb && cb.getAttribute("data-layer")) {
          STATE.layers[cb.getAttribute("data-layer")] = Boolean(cb.checked);
          renderMap();
        }
      });
    }

    var svg = document.getElementById("world-map");
    if (svg) {
      svg.addEventListener("click", function (e) {
        var g = e.target.closest("[data-select]");
        if (!g) return;
        var sel = g.getAttribute("data-select");
        if (sel) {
          window.location.hash = sel;
        }
      });
    }

    window.addEventListener("hashchange", syncFromHash);
  }

  bindEvents();
  T.mountSettings(document.getElementById("time-settings"));
  T.onChange(render);

  Promise.all([
    fetch("data/snapshot.json").then(function (r) {
      if (!r.ok) throw new Error("snapshot.json: HTTP " + r.status);
      return r.json();
    }),
    fetch("data/feed.geojson").then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; })
  ])
    .then(function (res) {
      STATE.snap = res[0];
      STATE.feed = res[1];
      syncFromHash();
    })
    .catch(function (err) {
      var banner = document.getElementById("mode-banner");
      if (banner) {
        banner.className = "mode-banner problems";
        banner.textContent = "Could not load Blackout's data (" + (err && err.message ? err.message : "unknown error") +
          "). Nothing below is current.";
      }
    });
})();
