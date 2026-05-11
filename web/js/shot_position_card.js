// shot_position_card.js
//
// Renders a unified ShotPosition (relative or absolute) into a DOM
// element. Drop-in for the result page:
//
//   import { renderShotPositionCard } from "./shot_position_card.js";
//   renderShotPositionCard(container, shot.position, userLatLon);
//
// `relative` -> compass dial + "原地附近 · 4.2 m" subtitle.
// `absolute` -> Leaflet (lazy-loaded from CDN) with user + shot pin
//               and a "走 78 m · ≈ 1 分钟" subtitle.

let leafletPromise = null;

function loadLeaflet() {
  if (leafletPromise) return leafletPromise;
  leafletPromise = new Promise((resolve, reject) => {
    if (typeof window.L !== "undefined") return resolve(window.L);
    const css = document.createElement("link");
    css.rel = "stylesheet";
    css.href = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css";
    document.head.appendChild(css);
    const js = document.createElement("script");
    js.src = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
    js.onload = () => resolve(window.L);
    js.onerror = reject;
    document.head.appendChild(js);
  });
  return leafletPromise;
}

function badgeFor(position) {
  const pct = Math.round((position.confidence || 0.5) * 100);
  const labels = {
    poi_kb:       "权威 POI",
    poi_online:   "AMap/OSM",
    poi_ugc:      "用户验证",
    poi_indoor:   "室内热点",
    sfm_ios:      "漫游验证",
    sfm_web:      "估算路径",
    triangulated: "远景三角化",
    recon3d:      "3D 重建",
    llm_relative: "AI 推断",
  };
  return `${labels[position.source] || position.source} · ${pct}%`;
}

function summary(position) {
  if (position.kind === "relative") {
    const d = position.distance_m == null ? "—" : `${position.distance_m.toFixed(1)} m`;
    return `原地附近 · ${d}`;
  }
  if (position.walk_distance_m != null) {
    const mins = position.est_walk_minutes ?? (position.walk_distance_m / 80);
    return `走 ${Math.round(position.walk_distance_m)} m · ≈ ${Math.max(1, Math.round(mins))} 分钟`;
  }
  return position.name_zh || "外部机位";
}

export function renderShotPositionCard(container, position, userLatLon) {
  if (!container || !position) return;
  container.innerHTML = "";
  container.classList.add("shot-position-card");

  const head = document.createElement("div");
  head.className = "spc-head";
  head.innerHTML = `
    <div class="spc-name">${escapeHTML(position.name_zh || (position.kind === "relative" ? "原地附近机位" : "外部机位"))}</div>
    <div class="spc-summary">${escapeHTML(summary(position))}</div>
    <div class="spc-badge">${escapeHTML(badgeFor(position))}</div>
  `;
  container.appendChild(head);

  if (position.kind === "relative") {
    const dial = document.createElement("div");
    dial.className = "spc-compass";
    const az = position.azimuth_deg ?? 0;
    dial.innerHTML = `
      <svg viewBox="-50 -50 100 100" width="64" height="64">
        <circle r="46" fill="none" stroke="rgba(0,0,0,0.15)" stroke-width="2"/>
        <text y="-32" text-anchor="middle" font-size="10" fill="currentColor" opacity="0.6">N</text>
        <g transform="rotate(${az})">
          <polygon points="0,-38 6,8 0,2 -6,8" fill="#1976d2"/>
        </g>
      </svg>
      <div class="spc-azimuth">方位 ${Math.round(az)}°</div>
    `;
    container.appendChild(dial);
    return;
  }

  // Absolute — render a Leaflet map.
  const mapEl = document.createElement("div");
  mapEl.className = "spc-map";
  mapEl.style.height = "160px";
  mapEl.style.borderRadius = "12px";
  mapEl.style.marginTop = "8px";
  container.appendChild(mapEl);
  if (position.walkability_note_zh) {
    const note = document.createElement("div");
    note.className = "spc-note";
    note.style.fontSize = "12px";
    note.style.color = "rgba(0,0,0,0.55)";
    note.style.marginTop = "6px";
    note.textContent = position.walkability_note_zh;
    container.appendChild(note);
  }

  loadLeaflet().then((L) => {
    const shotLatLon = [position.lat, position.lon];
    const center = userLatLon
      ? [(userLatLon[0] + shotLatLon[0]) / 2,
         (userLatLon[1] + shotLatLon[1]) / 2]
      : shotLatLon;
    const map = L.map(mapEl, { zoomControl: false, attributionControl: false })
      .setView(center, 17);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
    }).addTo(map);
    L.marker(shotLatLon, { title: position.name_zh || "shot" }).addTo(map);
    if (userLatLon) {
      L.circleMarker(userLatLon, { radius: 6, color: "#1976d2", fillOpacity: 0.9 })
        .addTo(map);
      const bounds = L.latLngBounds([shotLatLon, userLatLon]).pad(0.4);
      map.fitBounds(bounds);
    }
  }).catch((e) => {
    mapEl.textContent = "地图加载失败";
    console.warn("leaflet load failed", e);
  });
}

function escapeHTML(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}
