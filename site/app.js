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
        '<div class="tile"><span class="k">As Of</span><span class="v">' + esc(when(snap.generated_at)) + "</span></div>"
      ].join("");
    }

    renderMap();
    renderMapDetail();

    // Filter corridors
    var filteredCorr = corr.filter(function (c) {
      if (STATE.filter === "ooni") return false;
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
      if (STATE.filter === "corridors" || STATE.filter === "ooni") return false;
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
      if (STATE.filter === "corridors" || STATE.filter === "corroborated" || STATE.filter === "tips") return false;
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
      if (STATE.filter === "ooni") return false;
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
    if (h && h !== "assessments-h") {
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
