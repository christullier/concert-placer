"use strict";

const COLORS = {
  drivable: "#22c55e",
  soldOut: "#f59e0b",
  unreachable: "#ef4444",
  start: "#8b5cf6",
};

const TOUR_LOADING_MESSAGES = [
  "Reading artist page…",
  "Fetching tour dates…",
  "Geocoding venues…",
  "Measuring drives…",
];

const ARTIST_SEARCH_LOADING_MESSAGES = [
  "Searching MusicBrainz…",
  "Collecting artist matches…",
];

const ARTIST_RESOLVE_LOADING_MESSAGES = [
  "Reading MusicBrainz links…",
  "Trying top artist sites…",
  "Fetching tour dates…",
  "Geocoding venues…",
];

const state = {
  concerts: [],
  artistCandidates: [],
  start: null,
  artist: null,
  sortKey: "date",
  selectedId: null,
  loading: false,
};

let map;
let markerLayer;
const markersById = new Map();
let loadingTimer = null;

const el = (id) => document.getElementById(id);

/* ---------- map ---------- */

function initMap() {
  map = L.map("map", { zoomControl: true }).setView([39.5, -98.35], 4);
  L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
    maxZoom: 19,
    attribution:
      '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
  }).addTo(map);
  markerLayer = L.layerGroup().addTo(map);
  addLegend();
}

function addLegend() {
  const legend = L.control({ position: "bottomright" });
  legend.onAdd = () => {
    const div = L.DomUtil.create("div", "legend");
    div.innerHTML = [
      ["drivable", "Drivable"],
      ["soldOut", "Sold out"],
      ["unreachable", "Unreachable"],
      ["start", "Start"],
    ]
      .map(
        ([key, label]) =>
          `<div><span class="dot" style="background:${COLORS[key]}"></span>${label}</div>`
      )
      .join("");
    return div;
  };
  legend.addTo(map);
}

function concertColor(concert) {
  if (concert.is_sold_out) return COLORS.soldOut;
  if (!concert.is_drivable) return COLORS.unreachable;
  return COLORS.drivable;
}

function makePin(color, size = 16) {
  return L.divIcon({
    className: "",
    html: `<div class="map-pin" style="width:${size}px;height:${size}px;background:${color}"></div>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
  });
}

function concertId(concert, index) {
  return `${index}-${concert.venue}-${concert.start_date}`;
}

/* ---------- rendering ---------- */

function sortedConcerts() {
  const concerts = state.concerts.map((concert, index) => ({
    concert,
    id: concertId(concert, index),
  }));
  if (state.sortKey === "distance") {
    concerts.sort(
      (a, b) => (a.concert.distance ?? Infinity) - (b.concert.distance ?? Infinity)
    );
  } else {
    concerts.sort((a, b) => a.concert.start_date.localeCompare(b.concert.start_date));
  }
  return concerts;
}

function formatDate(iso) {
  if (!iso) return "Date TBA";
  const date = new Date(`${iso.slice(0, 10)}T12:00:00`);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function statusChip(concert) {
  if (concert.is_sold_out) return '<span class="chip chip-soldout">Sold out</span>';
  if (!concert.is_drivable) return '<span class="chip chip-unreachable">Unreachable</span>';
  return "";
}

function render() {
  renderCards();
  renderMarkers();
}

function renderCards() {
  const cards = el("cards");
  cards.innerHTML = "";

  for (const { concert, id } of sortedConcerts()) {
    const li = document.createElement("li");
    li.className = "card" + (state.selectedId === id ? " selected" : "");
    li.dataset.id = id;

    const distance =
      concert.distance != null
        ? `<span class="badge-distance">${Math.round(concert.distance)} mi</span>`
        : "";
    const error =
      !concert.is_drivable && concert.navigation_error
        ? `<div class="card-error">${escapeHtml(concert.navigation_error)}</div>`
        : "";

    li.innerHTML = `
      <div class="card-top">
        <span class="card-venue">${escapeHtml(concert.venue)}</span>
        ${distance}
      </div>
      <div class="card-city">${escapeHtml(concert.city)}</div>
      <div class="card-bottom">
        <span class="card-date">${formatDate(concert.start_date)}</span>
        ${statusChip(concert)}
      </div>
      ${error}
    `;
    li.addEventListener("click", () => select(id, { pan: true }));
    cards.appendChild(li);
  }

  const summary = el("results-summary");
  const total = state.concerts.length;
  const drivable = state.concerts.filter(
    (concert) => concert.is_drivable && !concert.is_sold_out
  ).length;
  summary.textContent = total
    ? `${total} show${total === 1 ? "" : "s"} · ${drivable} drivable`
    : "No upcoming shows found";
}

function renderMarkers() {
  markerLayer.clearLayers();
  markersById.clear();

  const bounds = [];

  if (state.start && state.start.lat != null) {
    const startMarker = L.marker([state.start.lat, state.start.lng], {
      icon: makePin(COLORS.start, 18),
      zIndexOffset: 1000,
    }).bindPopup(`<strong>Start</strong><br>${escapeHtml(state.start.address ?? "")}`);
    markerLayer.addLayer(startMarker);
    bounds.push([state.start.lat, state.start.lng]);
  }

  for (const { concert, id } of sortedConcerts()) {
    if (concert.lat == null || concert.lng == null) continue;
    const marker = L.marker([concert.lat, concert.lng], {
      icon: makePin(concertColor(concert)),
    }).bindPopup(popupHtml(concert));
    marker.on("click", () => select(id, { pan: false }));
    markerLayer.addLayer(marker);
    markersById.set(id, marker);
    bounds.push([concert.lat, concert.lng]);
  }

  if (bounds.length) {
    map.fitBounds(bounds, { padding: [50, 50], maxZoom: 10 });
  }
}

function popupHtml(concert) {
  const lines = [
    `<strong>${escapeHtml(concert.venue)}</strong>`,
    escapeHtml(concert.city),
    formatDate(concert.start_date),
  ];
  if (concert.distance != null) lines.push(`${Math.round(concert.distance)} mi drive`);
  if (concert.is_sold_out) lines.push("Sold out");
  else if (!concert.is_drivable) lines.push("Unreachable by road");
  return lines.join("<br>");
}

function select(id, { pan }) {
  state.selectedId = id;

  document.querySelectorAll(".card").forEach((card) => {
    card.classList.toggle("selected", card.dataset.id === id);
  });
  document
    .querySelector(`.card[data-id="${CSS.escape(id)}"]`)
    ?.scrollIntoView({ block: "nearest", behavior: "smooth" });

  markersById.forEach((marker, markerId) => {
    marker.getElement()?.firstChild?.classList.toggle("selected", markerId === id);
  });

  const marker = markersById.get(id);
  if (marker) {
    if (pan) map.panTo(marker.getLatLng());
    marker.openPopup();
  }
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text ?? "";
  return div.innerHTML;
}

/* ---------- loading / errors ---------- */

function setLoading(loading, messages = TOUR_LOADING_MESSAGES) {
  state.loading = loading;
  el("submit").disabled = loading;
  const status = el("status");
  clearInterval(loadingTimer);
  loadingTimer = null;

  if (loading) {
    el("error").hidden = true;
    el("results-section").hidden = false;
    el("artist-header").hidden = true;
    el("cards").innerHTML = '<li class="card skeleton"></li>'.repeat(4);

    let messageIndex = 0;
    status.textContent = messages[0];
    status.hidden = false;
    loadingTimer = setInterval(() => {
      messageIndex = Math.min(messageIndex + 1, messages.length - 1);
      status.textContent = messages[messageIndex];
    }, 2500);
  } else {
    status.textContent = "";
    status.hidden = true;
  }
}

function showError(message) {
  const banner = el("error");
  banner.textContent = message;
  banner.hidden = false;
  el("results-section").hidden = true;
  el("candidate-section").hidden = true;
}

async function readErrorDetail(response) {
  try {
    const body = await response.json();
    if (typeof body.detail === "string") return body.detail;
    if (body.detail?.message) return body.detail.message;
  } catch {
    /* not JSON */
  }
  if (response.status === 422) return "That page doesn't look like a Seated artist page.";
  if (response.status >= 500) return "Lookup failed — check your connection and try again.";
  return `Request failed (${response.status}).`;
}

/* ---------- search ---------- */

async function search() {
  const artistQuery = el("artist-url").value.trim();
  const startLocation = el("start-location").value.trim();
  if (!artistQuery || !startLocation || state.loading) return;

  if (isLikelyUrl(artistQuery)) {
    await lookupConcerts(normalizeArtistUrl(artistQuery), startLocation);
    return;
  }

  await searchArtistCandidates(artistQuery);
}

async function searchArtistCandidates(artistQuery) {
  setLoading(true, ARTIST_SEARCH_LOADING_MESSAGES);
  el("candidate-section").hidden = true;
  try {
    const response = await fetch(`/api/artist-search?q=${encodeURIComponent(artistQuery)}`);
    if (!response.ok) {
      showError(await readErrorDetail(response));
      return;
    }

    const data = await response.json();
    state.artistCandidates = data.artists ?? [];
    renderArtistCandidates();
  } catch (error) {
    showError(`Artist search failed: ${error.message}`);
  } finally {
    setLoading(false);
  }
}

async function resolveArtistAndLookup(artist) {
  const startLocation = el("start-location").value.trim();
  if (!startLocation || state.loading) return;

  setLoading(true, ARTIST_RESOLVE_LOADING_MESSAGES);
  try {
    const resolveResponse = await fetch("/api/resolve-artist", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mbid: artist.mbid }),
    });

    if (!resolveResponse.ok) {
      showError(await readErrorDetail(resolveResponse));
      return;
    }

    const resolved = await resolveResponse.json();
    if (!resolved.artist_url) {
      showResolutionFailure(artist, resolved.tried_urls ?? []);
      return;
    }

    el("artist-url").value = resolved.artist_url;
    await lookupConcerts(resolved.artist_url, startLocation, { manageLoading: false });
  } catch (error) {
    showError(`Artist resolution failed: ${error.message}`);
  } finally {
    setLoading(false);
  }
}

async function lookupConcerts(artistUrl, startLocation, { manageLoading = true } = {}) {
  if (manageLoading) setLoading(true, TOUR_LOADING_MESSAGES);
  try {
    const response = await fetch("/api/concerts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ artist_url: artistUrl, start_location: startLocation }),
    });

    if (!response.ok) {
      showError(await readErrorDetail(response));
      return;
    }

    const data = await response.json();
    state.concerts = data.concerts;
    state.start = data.start;
    state.artist = data.artist;
    state.selectedId = null;

    el("candidate-section").hidden = true;
    el("results-section").hidden = false;
    renderArtistHeader();
    render();
    loadSavedArtists();
  } catch (error) {
    showError(`Lookup failed: ${error.message}`);
  } finally {
    if (manageLoading) setLoading(false);
  }
}

function renderArtistCandidates() {
  const section = el("candidate-section");
  const list = el("candidate-list");
  list.innerHTML = "";

  if (!state.artistCandidates.length) {
    showError("No MusicBrainz artist matches found.");
    return;
  }

  el("error").hidden = true;
  el("results-section").hidden = true;
  section.hidden = false;

  for (const artist of state.artistCandidates) {
    const li = document.createElement("li");
    li.className = "candidate-item";
    const meta = [
      artist.disambiguation,
      artist.type,
      artist.country ?? artist.area,
      artist.score != null ? `${artist.score}% match` : "",
    ].filter(Boolean);
    li.innerHTML = `
      <button type="button" class="candidate-button">
        <span class="candidate-name">${escapeHtml(artist.name)}</span>
        <span class="candidate-meta">${escapeHtml(meta.join(" · "))}</span>
      </button>
    `;
    li.querySelector("button").addEventListener("click", () => resolveArtistAndLookup(artist));
    list.appendChild(li);
  }
}

function showResolutionFailure(artist, triedUrls) {
  const urls = triedUrls.map((candidate) => candidate.url).filter(Boolean);
  const tried = urls.length ? `\nTried:\n${urls.join("\n")}` : "\nNo usable MusicBrainz URLs found.";
  showError(`Found ${artist.name}, but none of the top 3 linked sites had Seated tour data.${tried}`);
}

function isLikelyUrl(value) {
  return /^https?:\/\//i.test(value) || /^www\./i.test(value);
}

function normalizeArtistUrl(value) {
  if (/^www\./i.test(value)) return `https://${value}`;
  return value;
}

function renderArtistHeader() {
  const header = el("artist-header");
  const avatar = el("artist-avatar");
  const name = state.artist?.name;

  el("artist-name").textContent = name ?? hostnameOf(el("artist-url").value);
  header.hidden = false;

  if (state.artist?.image_url) {
    avatar.src = state.artist.image_url;
    avatar.hidden = false;
  } else {
    avatar.hidden = true;
  }
}

function hostnameOf(url) {
  try {
    return new URL(url).hostname;
  } catch {
    return url;
  }
}

/* ---------- saved artists ---------- */

async function loadSavedArtists() {
  try {
    const response = await fetch("/api/artists");
    if (!response.ok) return;
    const data = await response.json();
    renderSavedArtists(data.artists ?? []);
  } catch {
    /* non-fatal */
  }
}

function renderSavedArtists(artists) {
  const section = el("saved-section");
  const list = el("saved-list");
  section.hidden = artists.length === 0;
  list.innerHTML = "";

  for (const artist of artists) {
    const li = document.createElement("li");
    li.className = "saved-chip";
    li.title = artist.url;
    li.innerHTML = `
      <span class="chip-name">${escapeHtml(artist.name)}</span>
      <span class="chip-when">${relativeTime(artist.last_checked)}</span>
      <button class="chip-delete" type="button" aria-label="Remove ${escapeHtml(artist.name)}">✕</button>
    `;
    li.addEventListener("click", () => {
      el("artist-url").value = artist.url;
      search();
    });
    li.querySelector(".chip-delete").addEventListener("click", async (event) => {
      event.stopPropagation();
      await fetch(`/api/artists/${encodeURIComponent(artist.id)}`, { method: "DELETE" });
      loadSavedArtists();
    });
    list.appendChild(li);
  }
}

function relativeTime(iso) {
  if (!iso) return "";
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000;
  if (seconds < 90) return "just now";
  const minutes = seconds / 60;
  if (minutes < 90) return `${Math.round(minutes)}m ago`;
  const hours = minutes / 60;
  if (hours < 36) return `${Math.round(hours)}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

/* ---------- init ---------- */

async function loadDefaults() {
  let configStartLocation = "";
  try {
    const response = await fetch("/api/config");
    if (!response.ok) throw new Error("Config unavailable");
    const config = await response.json();
    configStartLocation = config.start_location ?? "";
  } catch {
    /* non-fatal */
  }

  try {
    const response = await fetch("/api/location-default");
    if (!response.ok) throw new Error("Location unavailable");
    const location = await response.json();
    if (location.location && !el("start-location").value) {
      el("start-location").value = location.location;
      return;
    }
  } catch {
    /* non-fatal */
  }

  if (configStartLocation && !el("start-location").value) {
    el("start-location").value = configStartLocation;
  }
}

function initSortControls() {
  document.querySelectorAll(".segmented button").forEach((button) => {
    button.addEventListener("click", () => {
      state.sortKey = button.dataset.sort;
      document
        .querySelectorAll(".segmented button")
        .forEach((other) => other.classList.toggle("active", other === button));
      render();
    });
  });
}

initMap();
initSortControls();
el("search-form").addEventListener("submit", (event) => {
  event.preventDefault();
  search();
});
loadDefaults();
loadSavedArtists();
