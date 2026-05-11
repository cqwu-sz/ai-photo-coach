// walk_route_card.js (W3.2)
//
// Lazy-loads Leaflet only when a card actually needs to render a route,
// then paints a polyline + foldable step list. The route polyline format
// matches the backend route_planner: "lon,lat;lon,lat;..." chunks
// joined by ";".

let leafletReady = null;

function ensureLeaflet() {
  if (leafletReady) return leafletReady;
  leafletReady = new Promise((resolve, reject) => {
    if (typeof window.L !== "undefined") return resolve(window.L);
    const css = document.createElement("link");
    css.rel = "stylesheet";
    css.href = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css";
    document.head.appendChild(css);
    const s = document.createElement("script");
    s.src = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
    s.onload = () => resolve(window.L);
    s.onerror = (e) => reject(e);
    document.head.appendChild(s);
  });
  return leafletReady;
}

function decodePolyline(s) {
  if (!s) return [];
  const out = [];
  for (const chunk of s.split(";")) {
    const [lon, lat] = chunk.split(",").map(Number);
    if (Number.isFinite(lon) && Number.isFinite(lat)) out.push([lat, lon]);
  }
  return out;
}

export async function renderWalkRouteCard(container, opts) {
  const { userLat, userLon, target, route } = opts;
  if (!container || !target || !route) return null;
  const card = document.createElement("div");
  card.className = "walk-route-card";
  card.innerHTML = `
    <div class="walk-route-card__head">
      <span>步行 ${route.distance_m.toFixed(0)} m · ${route.duration_min.toFixed(1)} 分钟</span>
      <span class="walk-route-card__provider">来源：${route.provider || "amap"}</span>
    </div>
    <div class="walk-route-card__map" style="height:180px"></div>
    <details class="walk-route-card__steps">
      <summary>展开 ${(route.steps || []).length} 步</summary>
      <ol>
        ${(route.steps || []).map(s =>
          `<li><div>${s.instruction_zh}</div>
           <small>${s.distance_m.toFixed(0)} m · ${s.duration_s.toFixed(0)}s</small></li>`
        ).join("")}
      </ol>
    </details>
  `;
  container.appendChild(card);
  const mapEl = card.querySelector(".walk-route-card__map");
  try {
    const L = await ensureLeaflet();
    const coords = decodePolyline(route.polyline);
    const center = [
      (userLat + (target.lat ?? userLat)) / 2,
      (userLon + (target.lon ?? userLon)) / 2,
    ];
    const map = L.map(mapEl, { zoomControl: false, attributionControl: false })
      .setView(center, 16);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png").addTo(map);
    L.marker([userLat, userLon]).addTo(map).bindTooltip("你");
    if (target.lat != null && target.lon != null) {
      L.marker([target.lat, target.lon]).addTo(map)
        .bindTooltip(target.name_zh || "机位");
    }
    if (coords.length >= 2) {
      L.polyline(coords, { color: "#0080ff", weight: 4 }).addTo(map);
      map.fitBounds(coords);
    }
  } catch (e) {
    mapEl.textContent = "地图加载失败：" + (e?.message || e);
  }
  return { destroy() { card.remove(); } };
}
