const form = document.getElementById("search-form");
const artistInput = document.getElementById("artist-url");
const startInput = document.getElementById("start-location");
const submitBtn = document.getElementById("submit");
const statusEl = document.getElementById("status");
const cardsEl = document.getElementById("cards");

let map;
let markerLayer;
const markersById = new Map();

function initMap() {
  map = L.map("map", { scrollWheelZoom: true }).setView([39.5, -98.35], 4);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap contributors",
  }).addTo(map);
  markerLayer = L.layerGroup().addTo(map);
}

// Colored circle pins without external image assets.
function pin(color) {
  return L.divIcon({
    className: "",
    html: `<div style="width:16px;height:16px;border-radius:50%;background:${color};
      border:2px solid #fff;box-shadow:0 0 0 1px rgba(0,0,0,0.4)"></div>`,
    iconSize: [16, 16],
    iconAnchor: [8, 8],
  });
}

const COLORS = {
  drivable: "#22c55e",
  soldout: "#f59e0b",
  unreachable: "#ef4444",
  start: "#38bdf8",
};

function statusOf(c) {
  if (c.is_sold_out) return "soldout";
  if (!c.is_drivable) return "unreachable";
  return "drivable";
}

function setStatus(msg, isError = false) {
  statusEl.textContent = msg;
  statusEl.classList.toggle("error", isError);
}

function selectCard(id) {
  document.querySelectorAll(".card.selected").forEach((el) => el.classList.remove("selected"));
  const card = document.querySelector(`.card[data-id="${id}"]`);
  if (card) {
    card.classList.add("selected");
    card.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
  const marker = markersById.get(id);
  if (marker) {
    map.setView(marker.getLatLng(), Math.max(map.getZoom(), 9), { animate: true });
    marker.openPopup();
  }
}

function render(data) {
  markerLayer.clearLayers();
  markersById.clear();
  cardsEl.innerHTML = "";

  const bounds = [];

  // Origin marker
  const start = data.start;
  if (start && start.lat != null) {
    const m = L.marker([start.lat, start.lng], { icon: pin(COLORS.start) })
      .bindPopup(`<b>Start</b><br>${start.address || ""}`);
    markerLayer.addLayer(m);
    bounds.push([start.lat, start.lng]);
  }

  const concerts = data.concerts || [];
  concerts.forEach((c, i) => {
    const id = String(i);
    const kind = statusOf(c);

    if (c.lat != null && c.lng != null) {
      const m = L.marker([c.lat, c.lng], { icon: pin(COLORS[kind]) })
        .bindPopup(`<b>${c.venue}</b><br>${c.city}<br>${c.start_date}`);
      m.on("click", () => selectCard(id));
      markerLayer.addLayer(m);
      markersById.set(id, m);
      bounds.push([c.lat, c.lng]);
    }

    const li = document.createElement("li");
    li.className = `card ${kind === "drivable" ? "" : kind}`.trim();
    li.dataset.id = id;

    let distance = "";
    if (c.is_sold_out) distance = `<span class="badge soldout">Sold out</span>`;
    else if (!c.is_drivable) distance = `<span class="badge unreachable">No route</span>`;
    else if (c.distance != null) distance = `<span class="distance">${c.distance} mi</span>`;
    else distance = `<span class="distance">—</span>`;

    li.innerHTML = `
      <div class="venue">${c.venue || "Unknown venue"}</div>
      <div class="city">${c.city || ""}</div>
      <div class="meta">
        <span class="date">${c.start_date || ""}</span>
        ${distance}
      </div>`;
    li.addEventListener("click", () => selectCard(id));
    cardsEl.appendChild(li);
  });

  if (bounds.length === 1) map.setView(bounds[0], 10);
  else if (bounds.length > 1) map.fitBounds(bounds, { padding: [40, 40] });

  setStatus(`${concerts.length} concert${concerts.length === 1 ? "" : "s"} found`);
}

async function search() {
  const artist_url = artistInput.value.trim();
  const start_location = startInput.value.trim();
  if (!artist_url || !start_location) return;

  submitBtn.disabled = true;
  setStatus("Searching…");
  cardsEl.innerHTML = "";

  try {
    const res = await fetch("/api/concerts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ artist_url, start_location }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `Request failed (${res.status})`);
    render(data);
  } catch (err) {
    setStatus(err.message || "Something went wrong", true);
  } finally {
    submitBtn.disabled = false;
  }
}

async function loadDefaults() {
  try {
    const res = await fetch("/api/config");
    const cfg = await res.json();
    if (cfg.artist_url) artistInput.value = cfg.artist_url;
    if (cfg.start_location) startInput.value = cfg.start_location;
  } catch (_) {
    /* defaults are optional */
  }
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  search();
});

initMap();
loadDefaults();
