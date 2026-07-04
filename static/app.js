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
  filters: {
    hideSoldOut: true,
    hideUnreachable: true,
    maxDistance: null,
    dateFrom: null,
    dateTo: null,
  },
  selectedId: null,
  loading: false,
};

// The fully-open state that "Clear filters" resets to (everything visible).
// The app *starts* feasible-first (state.filters above), but clearing reveals all.
const OPEN_FILTERS = {
  hideSoldOut: false,
  hideUnreachable: false,
  maxDistance: null,
  dateFrom: null,
  dateTo: null,
};

let map;
let markerLayer;
const markersById = new Map();
let loadingTimer = null;
let lastBounds = null;

const mobileQuery = window.matchMedia("(max-width: 900px)");

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

function matchesFilters(concert) {
  const f = state.filters;
  if (f.hideSoldOut && concert.is_sold_out) return false;
  if (f.hideUnreachable && !concert.is_drivable) return false;
  if (f.maxDistance != null && concert.distance != null && concert.distance > f.maxDistance)
    return false;
  if (f.dateFrom && concert.start_date < f.dateFrom) return false;
  if (f.dateTo && concert.start_date > f.dateTo) return false;
  return true;
}

function sortedConcerts() {
  const concerts = state.concerts
    .map((concert, index) => ({
      concert,
      id: concertId(concert, index),
    }))
    .filter(({ concert }) => matchesFilters(concert));
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
  const date = parseDateOnly(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function parseDateOnly(iso) {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso ?? "");
  if (!match) return new Date(`${iso?.slice(0, 10)}T12:00:00`);
  return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]), 12);
}

function daysUntilDate(iso) {
  const date = parseDateOnly(iso);
  if (Number.isNaN(date.getTime())) return null;
  const today = new Date();
  const todayUtc = Date.UTC(today.getFullYear(), today.getMonth(), today.getDate());
  const dateUtc = Date.UTC(date.getFullYear(), date.getMonth(), date.getDate());
  return Math.round((dateUtc - todayUtc) / 86400000);
}

function formatDaysUntil(iso) {
  const days = daysUntilDate(iso);
  if (days == null) return "Date TBA";
  if (days === 1) return "1 day";
  return `${days} days`;
}

function cardDateHtml(iso) {
  const actualDate = formatDate(iso);
  const daysUntil = formatDaysUntil(iso);
  return `
    <button
      type="button"
      class="card-date"
      aria-label="${escapeHtml(actualDate)}"
      aria-expanded="false"
    >
      <span class="card-date-text">${escapeHtml(daysUntil)}</span>
      <span class="date-popover" role="tooltip">${escapeHtml(actualDate)}</span>
    </button>
  `;
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

  const visibleConcerts = sortedConcerts();
  for (const { concert, id } of visibleConcerts) {
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
        ${cardDateHtml(concert.start_date)}
        ${statusChip(concert)}
      </div>
      ${error}
    `;
    li.querySelector(".card-date")?.addEventListener("click", (event) => {
      event.stopPropagation();
      toggleDatePopover(event.currentTarget);
    });
    li.addEventListener("click", () => select(id, { pan: true }));
    cards.appendChild(li);
  }

  const summary = el("results-summary");
  const total = state.concerts.length;
  const visible = visibleConcerts.length;
  const clearLink = '<button type="button" class="filter-clear">Clear filters</button>';

  const filterRow = document.querySelector(".filter-row");
  if (filterRow) filterRow.hidden = total === 0;

  if (!total) {
    summary.textContent = "No upcoming shows found";
  } else if (visible === 0) {
    summary.innerHTML = `No shows match your filters · ${clearLink}`;
  } else if (visible < total) {
    summary.innerHTML = `Showing ${visible} of ${total} show${
      total === 1 ? "" : "s"
    } · ${clearLink}`;
  } else {
    const drivable = state.concerts.filter(
      (concert) => concert.is_drivable && !concert.is_sold_out
    ).length;
    summary.textContent = `${total} show${total === 1 ? "" : "s"} · ${drivable} drivable`;
  }

  summary.querySelector(".filter-clear")?.addEventListener("click", clearFilters);
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

  lastBounds = bounds.length ? bounds : null;
  if (lastBounds && !(mobileQuery.matches && document.body.dataset.view !== "map")) {
    map.fitBounds(lastBounds, { padding: [50, 50], maxZoom: 10 });
  }
}

/* ---------- mobile list/map toggle ---------- */

function setView(view) {
  document.body.dataset.view = view;
  document.querySelectorAll(".view-toggle button").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === view);
  });

  if (view === "map" && map) {
    // Leaflet must recompute dimensions now that its container is visible,
    // and re-fit the bounds it couldn't measure while hidden.
    map.invalidateSize();
    if (lastBounds) map.fitBounds(lastBounds, { padding: [50, 50], maxZoom: 10 });
  }
}

function initViewToggle() {
  document.querySelectorAll(".view-toggle button").forEach((button) => {
    button.addEventListener("click", () => setView(button.dataset.view));
  });
  document.body.dataset.view = "list";
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

  // On mobile a card tap (pan) reveals the map so the pin is actually visible.
  if (pan && mobileQuery.matches && document.body.dataset.view !== "map") {
    setView("map");
  }

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

function closeDatePopovers(except = null) {
  document.querySelectorAll(".card-date.open").forEach((button) => {
    if (button === except) return;
    button.classList.remove("open");
    button.setAttribute("aria-expanded", "false");
  });
}

function toggleDatePopover(button) {
  const willOpen = !button.classList.contains("open");
  closeDatePopovers(button);
  button.classList.toggle("open", willOpen);
  button.setAttribute("aria-expanded", String(willOpen));
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

  // Return to the list so loading progress and results are in view.
  setView("list");

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
      showResolutionFailure(artist, resolved);
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
    document.body.classList.add("has-results");
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

function showResolutionFailure(artist, resolved) {
  const triedUrls = resolved.tried_urls ?? [];
  const urls = triedUrls.map((candidate) => candidate.url).filter(Boolean);
  const tried = urls.length ? `\nTried:\n${urls.join("\n")}` : "\nNo usable MusicBrainz URLs found.";
  const unsupported = resolved.unsupported_provider;
  if (unsupported?.provider) {
    const provider = formatProviderName(unsupported.provider);
    showError(`Found ${artist.name}, but the linked tour source uses ${provider}, which is not supported for concert lookup yet.${tried}`);
    return;
  }
  showError(`Found ${artist.name}, but none of the linked sites had supported tour data.${tried}`);
}

function formatProviderName(provider) {
  const names = {
    axs: "AXS",
    bandsintown: "Bandsintown",
    dice: "Dice",
    eventbrite: "Eventbrite",
    songkick: "Songkick",
    "squarespace-events": "Squarespace Events",
    ticketmaster: "Ticketmaster",
  };
  return names[provider] ?? provider;
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
  document.querySelectorAll(".sort-row .segmented button").forEach((button) => {
    button.addEventListener("click", () => {
      state.sortKey = button.dataset.sort;
      document
        .querySelectorAll(".sort-row .segmented button")
        .forEach((other) => other.classList.toggle("active", other === button));
      render();
    });
  });
}

function updateDistanceLabel() {
  const slider = el("filter-distance");
  const label = el("filter-distance-label");
  if (!slider || !label) return;
  const atMax = Number(slider.value) >= Number(slider.max);
  label.textContent = atMax ? "Any drive" : `Within ${slider.value} mi`;
}

function syncFilterControls() {
  document.querySelectorAll(".filter-toggle").forEach((button) => {
    const on = state.filters[button.dataset.filter];
    button.classList.toggle("active", on);
    button.setAttribute("aria-pressed", on ? "true" : "false");
  });
  const slider = el("filter-distance");
  if (slider) {
    slider.value =
      state.filters.maxDistance == null ? slider.max : String(state.filters.maxDistance);
    updateDistanceLabel();
  }
  el("filter-date-from").value = state.filters.dateFrom ?? "";
  el("filter-date-to").value = state.filters.dateTo ?? "";
}

function clearFilters() {
  state.filters = { ...OPEN_FILTERS };
  syncFilterControls();
  render();
}

function initFilterControls() {
  document.querySelectorAll(".filter-toggle").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.filter;
      const next = !state.filters[key];
      state.filters[key] = next;
      button.classList.toggle("active", next);
      button.setAttribute("aria-pressed", next ? "true" : "false");
      render();
    });
  });

  const slider = el("filter-distance");
  slider?.addEventListener("input", () => {
    const atMax = Number(slider.value) >= Number(slider.max);
    state.filters.maxDistance = atMax ? null : Number(slider.value);
    updateDistanceLabel();
    render();
  });

  const from = el("filter-date-from");
  const to = el("filter-date-to");
  const today = new Date().toISOString().slice(0, 10);
  if (from) from.min = today;
  if (to) to.min = today;
  from?.addEventListener("change", () => {
    state.filters.dateFrom = from.value || null;
    render();
  });
  to?.addEventListener("change", () => {
    state.filters.dateTo = to.value || null;
    render();
  });

  updateDistanceLabel();
}

initMap();
initSortControls();
initFilterControls();
initViewToggle();
el("search-form").addEventListener("submit", (event) => {
  event.preventDefault();
  search();
});
document.addEventListener("click", () => closeDatePopovers());
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeDatePopovers();
});
loadDefaults();
loadSavedArtists();
