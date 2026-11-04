/* Blackout UI — renders snapshot.json */
(function () {
  "use strict";

  function render(snap) {
    var outages = snap.outages || [];
    var ooni = snap.censorship || [];
    var corr = snap.corridor_alerts || [];

    var tiles = document.getElementById("tiles");
    if (tiles) {
      tiles.innerHTML = [
        '<div class="tile"><span class="k">Active Outages</span><span class="v">' + outages.length + "</span></div>",
        '<div class="tile"><span class="k">Cable Corridor Alerts</span><span class="v">' + corr.length + "</span></div>",
        '<div class="tile"><span class="k">OONI Surges (&ge;15%)</span><span class="v">' + ooni.length + "</span></div>",
        '<div class="tile"><span class="k">As Of (UTC)</span><span class="v">' + (snap.generated_at || "").slice(0, 16).replace("T", " ") + "</span></div>"
      ].join("");
    }

    var corrEl = document.getElementById("corridors");
    if (corrEl) {
      corrEl.innerHTML = corr.length ? corr.map(function (c) {
        return '<div class="row warn">' +
          '<div class="row-title"><strong>' + c.name + '</strong> <span class="badge badge-warn">Mean -' + c.mean_drop_pct + '%</span></div>' +
          '<div class="small">' + c.summary + '</div>' +
          '<div class="small muted">Landing stations: ' + (c.landing_stations || []).join(" · ") + '</div>' +
        '</div>';
      }).join("") : '<p class="muted small">No multi-country cable corridor faults active.</p>';
    }

    var outEl = document.getElementById("outages");
    if (outEl) {
      outEl.innerHTML = outages.map(function (o) {
        return '<div class="row">' +
          '<div class="row-title"><strong>' + o.country_name + ' (' + o.country + ')</strong> <span class="badge">-' + o.drop_pct + '%</span></div>' +
          '<div class="small">' + (o.asn ? o.asn + ' — ' + o.asn_name + ': ' : '') + o.summary + '</div>' +
        '</div>';
      }).join("");
    }

    var ooniEl = document.getElementById("censorship");
    if (ooniEl) {
      ooniEl.innerHTML = ooni.map(function (c) {
        return '<div class="row">' +
          '<div class="row-title"><strong>' + c.country_name + ' (' + c.country + ')</strong> <span class="badge">' + (c.anomaly_rate * 100).toFixed(1) + '% anomaly</span></div>' +
          '<div class="small">' + c.summary + '</div>' +
        '</div>';
      }).join("");
    }
  }

  fetch("data/snapshot.json")
    .then(function (r) { return r.json(); })
    .then(render)
    .catch(function () {});
})();
