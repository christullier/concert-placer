"use strict";

const COLORS = {
  drivable: "#3348ff",
  soldOut: "#ff6044",
  unreachable: "#c83328",
  start: "#d8ff3e",
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
  parseStatus: "full",
  externalUrl: null,
  provider: null,
  sortKey: "distance",
  filters: {
    hideSoldOut: true,
    hideUnreachable: true,
    maxDistance: null,
    dateFrom: null,
    dateTo: null,
  },
  selectedId: null,
  loading: false,
  selectedCandidate: null,
  selectedArtistUrl: null,
  sheetPosition: "both",
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
let artistSearchTimer = null;
let artistSearchVersion = 0;
let activeCandidateIndex = -1;
let sheetDragStartY = null;
let sheetDragStartHeight = null;
let sheetDidDrag = false;
const artistSearchCache = new Map();
const artistResolutionCache = new Map();

const mobileQuery = window.matchMedia("(max-width: 900px)");

const el = (id) => document.getElementById(id);

/* ---------- map ---------- */

function initMap() {
  map = L.map("map", { zoomControl: false }).setView([39.5, -98.35], 4);
  L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
    maxZoom: 19,
    attribution:
      '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
  }).addTo(map);
  L.control.zoom({ position: "topright" }).addTo(map);
  markerLayer = L.layerGroup().addTo(map);
}

function isMapReady() {
  return Boolean(map && markerLayer);
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

function formatDateShort(iso) {
  if (!iso) return "Date TBA";
  const date = parseDateOnly(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const options = { weekday: "short", month: "short", day: "numeric" };
  if (date.getFullYear() !== new Date().getFullYear()) options.year = "numeric";
  return date.toLocaleDateString(undefined, options);
}

function formatDaysUntil(iso) {
  const days = daysUntilDate(iso);
  if (days == null || days < 0) return "";
  if (days === 0) return "today";
  if (days === 1) return "tomorrow";
  return `in ${days} days`;
}

function cardDateHtml(iso) {
  const daysUntil = formatDaysUntil(iso);
  return `
    <div class="card-date">
      <span class="card-date-text">${escapeHtml(formatDateShort(iso))}</span>
      ${daysUntil ? `<span class="card-date-days">${escapeHtml(daysUntil)}</span>` : ""}
    </div>
  `;
}

// Trim a trailing parenthetical that just repeats part of the venue name,
// e.g. "The Vogel at Count Basie Center for the Arts (The Vogel)".
function displayVenue(name) {
  const match = /^(.*\S)\s*\(([^)]+)\)$/.exec(name ?? "");
  if (match && match[1].toLowerCase().includes(match[2].toLowerCase())) return match[1];
  return name;
}

function statusChip(concert) {
  if (concert.is_sold_out) return '<span class="chip chip-soldout">Sold out</span>';
  if (!concert.is_drivable) return '<span class="chip chip-unreachable">Unreachable</span>';
  return "";
}

function isSafeTicketUrl(url) {
  return typeof url === "string" && /^https?:\/\//i.test(url);
}

function ticketLinkHtml(concert, className = "card-tickets") {
  if (!isSafeTicketUrl(concert.ticket_url)) return "";
  return `<a class="${className}" href="${escapeHtml(concert.ticket_url)}" target="_blank" rel="noopener noreferrer">Tickets</a>`;
}

function isLinkOnlyResults() {
  return state.parseStatus === "link_only";
}

function render() {
  renderExternalTourLink();
  renderCards();
  renderMarkers();
}

function renderExternalTourLink() {
  const block = el("external-tour-link");
  const copy = el("external-tour-copy");
  const cta = el("external-tour-cta");
  const controls = document.querySelector(".results-controls");
  const cards = el("cards");

  if (!isLinkOnlyResults()) {
    block.hidden = true;
    if (controls) controls.hidden = false;
    return;
  }

  const providerName = formatProviderName(state.provider);
  copy.textContent = `Shows are listed on ${providerName}, but this page doesn't expose dates we can map.`;
  cta.textContent = `View shows on ${providerName}`;
  cta.href = state.externalUrl ?? "#";
  block.hidden = false;
  if (controls) controls.hidden = true;
  cards.innerHTML = "";

  const summary = el("results-summary");
  summary.textContent = `Shows available on ${providerName} — open the site for dates and tickets`;
  el("sheet-peek-label").textContent = "Tour link";
}

function renderCards() {
  const cards = el("cards");
  if (isLinkOnlyResults()) {
    return;
  }
  cards.innerHTML = "";

  const visibleConcerts = sortedConcerts();
  for (const { concert, id } of visibleConcerts) {
    const li = document.createElement("li");
    li.className = "card" + (state.selectedId === id ? " selected" : "");
    li.dataset.id = id;
    li.tabIndex = 0;

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
        <span class="card-venue">${escapeHtml(displayVenue(concert.venue))}</span>
        ${distance}
      </div>
      <div class="card-city">${escapeHtml(concert.city)}</div>
      <div class="card-bottom">
        ${cardDateHtml(concert.start_date)}
        <div class="card-actions">
          ${ticketLinkHtml(concert)}
          ${statusChip(concert)}
        </div>
      </div>
      ${error}
    `;
    li.querySelector(".card-tickets")?.addEventListener("click", (event) => {
      event.stopPropagation();
    });
    li.addEventListener("click", () => select(id, { pan: true }));
    li.addEventListener("keydown", (event) => {
      if (event.target !== li || (event.key !== "Enter" && event.key !== " ")) return;
      event.preventDefault();
      select(id, { pan: true });
    });
    cards.appendChild(li);
  }

  const summary = el("results-summary");
  const total = state.concerts.length;
  const visible = visibleConcerts.length;
  el("sheet-peek-label").textContent = `${visible} show${visible === 1 ? "" : "s"}`;
  const clearLink = '<button type="button" class="filter-clear">Clear filters</button>';

  const controls = document.querySelector(".results-controls");
  if (controls) controls.hidden = total === 0;
  updateFilterBadge();

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
    summary.textContent = `${drivable} of ${total} drivable`;
  }

  summary.querySelector(".filter-clear")?.addEventListener("click", clearFilters);
}

function renderMarkers() {
  if (!isMapReady()) {
    markersById.clear();
    lastBounds = null;
    return;
  }

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

  if (isLinkOnlyResults()) {
    lastBounds = bounds.length ? bounds : null;
    if (lastBounds) fitMapToResults();
    return;
  }

  for (const { concert, id } of sortedConcerts()) {
    if (concert.lat == null || concert.lng == null) continue;
    const marker = L.marker([concert.lat, concert.lng], {
      icon: makePin(concertColor(concert)),
    }).bindPopup(popupHtml(concert));
    marker.on("click", () => select(id, { pan: false, fromMarker: true }));
    markerLayer.addLayer(marker);
    markersById.set(id, marker);
    bounds.push([concert.lat, concert.lng]);
  }

  lastBounds = bounds.length ? bounds : null;
  if (lastBounds) fitMapToResults();
}

/* ---------- immersive results / mobile sheet ---------- */

function sheetDetentHeights() {
  const viewport = window.innerHeight;
  return {
    map: 76,
    both: Math.min(Math.max(viewport * 0.48, 300), 520),
    list: viewport,
  };
}

function mapFitOptions() {
  if (!document.body.classList.contains("has-results")) {
    return { padding: [50, 50], maxZoom: 10 };
  }
  if (mobileQuery.matches) {
    const sheetHeight = sheetDetentHeights()[state.sheetPosition];
    return {
      paddingTopLeft: [42, 56],
      paddingBottomRight: [42, state.sheetPosition === "list" ? 56 : sheetHeight + 34],
      maxZoom: 10,
    };
  }
  return {
    paddingTopLeft: [460, 58],
    paddingBottomRight: [58, 58],
    maxZoom: 10,
  };
}

function fitMapToResults() {
  if (!map || !lastBounds) return;
  map.fitBounds(lastBounds, mapFitOptions());
}

function refreshMapLayout({ fit = true } = {}) {
  if (!map) return;
  requestAnimationFrame(() => {
    map.invalidateSize();
    if (fit) fitMapToResults();
  });
  window.setTimeout(() => {
    map.invalidateSize();
    if (fit) fitMapToResults();
  }, 460);
}

function setLandingInert(inert) {
  document.querySelectorAll(".brand, .search-panel, .saved").forEach((node) => {
    node.inert = inert;
    node.setAttribute("aria-hidden", inert ? "true" : "false");
  });
}

function updateSheetControls() {
  const handle = el("sheet-handle");
  const labels = {
    map: "Show concert list",
    both: "Expand concert list",
    list: "Show map and concert list",
  };
  handle.setAttribute("aria-label", labels[state.sheetPosition]);
  handle.setAttribute("aria-expanded", state.sheetPosition === "list" ? "true" : "false");
}

function setSheetPosition(position, { fit = false } = {}) {
  if (!Object.hasOwn(sheetDetentHeights(), position)) return;
  state.sheetPosition = position;
  document.body.dataset.sheet = position;
  const results = el("results-section");
  const content = document.querySelector(".results-content");
  results.style.removeProperty("height");
  results.classList.remove("is-dragging");
  content.inert = mobileQuery.matches && position === "map";
  content.setAttribute("aria-hidden", content.inert ? "true" : "false");
  updateSheetControls();
  if (mobileQuery.matches) refreshMapLayout({ fit });
}

function enterResultsMode() {
  setSheetPosition("both", { fit: false });
  setLandingInert(true);
  document.body.classList.add("has-results");
  document.body.dataset.view = "map";
  el("map-back").hidden = false;
  refreshMapLayout();
}

function exitResultsMode() {
  setLandingInert(false);
  document.body.classList.remove("has-results");
  el("map-back").hidden = true;
  delete document.body.dataset.sheet;
  document.body.dataset.view = "list";
  el("results-section").hidden = true;
  map?.closePopup();
  refreshMapLayout({ fit: false });
  window.setTimeout(() => {
    el("artist-url").focus();
    el("artist-url").select();
  }, 80);
}

function nearestSheetPosition(height) {
  const detents = sheetDetentHeights();
  return Object.entries(detents).reduce((nearest, [position, detentHeight]) =>
    Math.abs(height - detentHeight) < Math.abs(height - detents[nearest]) ? position : nearest
  , "both");
}

function onSheetPointerDown(event) {
  if (!mobileQuery.matches || event.button > 0) return;
  const results = el("results-section");
  sheetDragStartY = event.clientY;
  sheetDragStartHeight = results.getBoundingClientRect().height;
  sheetDidDrag = false;
  results.classList.add("is-dragging");
  el("sheet-handle").setPointerCapture(event.pointerId);
}

function onSheetPointerMove(event) {
  if (sheetDragStartY == null || !mobileQuery.matches) return;
  const delta = sheetDragStartY - event.clientY;
  if (Math.abs(delta) > 5) sheetDidDrag = true;
  const detents = sheetDetentHeights();
  const height = Math.min(Math.max(sheetDragStartHeight + delta, detents.map), detents.list);
  el("results-section").style.height = `${height}px`;
}

function onSheetPointerUp(event) {
  if (sheetDragStartY == null) return;
  const results = el("results-section");
  const height = results.getBoundingClientRect().height;
  sheetDragStartY = null;
  sheetDragStartHeight = null;
  if (el("sheet-handle").hasPointerCapture(event.pointerId)) {
    el("sheet-handle").releasePointerCapture(event.pointerId);
  }
  if (sheetDidDrag) setSheetPosition(nearestSheetPosition(height));
  else results.classList.remove("is-dragging");
}

function cycleSheetPosition() {
  if (sheetDidDrag) {
    sheetDidDrag = false;
    return;
  }
  const next = { map: "both", both: "list", list: "both" };
  setSheetPosition(next[state.sheetPosition]);
}

function popupHtml(concert) {
  const lines = [
    `<strong>${escapeHtml(displayVenue(concert.venue))}</strong>`,
    escapeHtml(concert.city),
    formatDate(concert.start_date),
  ];
  if (concert.distance != null) lines.push(`${Math.round(concert.distance)} mi drive`);
  if (concert.is_sold_out) lines.push("Sold out");
  else if (!concert.is_drivable) lines.push("Unreachable by road");
  const tickets = ticketLinkHtml(concert, "popup-tickets");
  if (tickets) lines.push(tickets);
  return lines.join("<br>");
}

function select(id, { pan, fromMarker = false }) {
  state.selectedId = id;

  if (fromMarker && mobileQuery.matches && state.sheetPosition === "map") {
    setSheetPosition("both", { fit: false });
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
    if (pan) zoomToRoute(marker.getLatLng());
    marker.openPopup();
  }
}

// Frame the map so both the start location and the selected concert are visible.
function zoomToRoute(destination) {
  if (!map) return;
  if (state.start && state.start.lat != null) {
    const bounds = L.latLngBounds([
      [state.start.lat, state.start.lng],
      [destination.lat, destination.lng],
    ]);
    map.fitBounds(bounds, mapFitOptions());
  } else {
    map.panTo(destination);
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
  document.body.classList.toggle("search-loading", loading);
  const status = el("status");
  clearInterval(loadingTimer);
  loadingTimer = null;

  if (loading) {
    el("error").hidden = true;
    el("results-section").hidden = true;
    el("artist-header").hidden = true;
    el("cards").innerHTML = "";

    let messageIndex = 0;
    status.textContent = messages[0];
    status.hidden = false;
    setSubmitLabel(messages[0]);
    loadingTimer = setInterval(() => {
      messageIndex = Math.min(messageIndex + 1, messages.length - 1);
      status.textContent = messages[messageIndex];
      setSubmitLabel(messages[messageIndex]);
    }, 2500);
  } else {
    status.textContent = "";
    status.hidden = true;
    updateSubmitLabel();
  }
}

function showError(message) {
  const banner = el("error");
  banner.textContent = message;
  banner.hidden = false;
  el("results-section").hidden = true;
  hideArtistCandidates();
  setLandingInert(false);
  document.body.classList.remove("has-results");
  delete document.body.dataset.sheet;
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
  if (!artistQuery || state.loading) return;

  if (!startLocation) {
    setLocationEditor(true);
    el("start-location").focus();
    showError("Add a starting location so we can compare the drive.");
    return;
  }
  syncLocationControl({ collapse: true });

  // Keep the search surface visible while loading; immersive mode starts on success.
  if (document.body.classList.contains("has-results")) exitResultsMode();

  if (isLikelyUrl(artistQuery)) {
    await lookupConcerts(normalizeArtistUrl(artistQuery), startLocation);
    return;
  }

  if (state.selectedCandidate && state.selectedCandidate.name === artistQuery) {
    await resolveArtistAndLookup(state.selectedCandidate);
    return;
  }

  if (state.artistCandidates.length) {
    await chooseArtistCandidate(0);
    return;
  }

  await searchArtistCandidates(artistQuery, { showEmptyError: true });
}

async function searchArtistCandidates(artistQuery, { showEmptyError = false } = {}) {
  const query = artistQuery.trim();
  if (query.length < 2 || isLikelyUrl(query)) {
    hideArtistCandidates();
    return [];
  }

  const requestVersion = ++artistSearchVersion;
  setArtistSearchPending(true);
  try {
    let artists = artistSearchCache.get(query.toLowerCase());
    if (!artists) {
      const response = await fetch(`/api/artist-search?q=${encodeURIComponent(query)}`);
      if (!response.ok) throw new Error(await readErrorDetail(response));
      const data = await response.json();
      artists = (data.artists ?? []).slice(0, 5);
      artistSearchCache.set(query.toLowerCase(), artists);
    }

    if (requestVersion !== artistSearchVersion || el("artist-url").value.trim() !== query) return [];
    state.artistCandidates = artists;
    renderArtistCandidates();
    if (artists[0]) prefetchArtistResolution(artists[0]);
    if (!artists.length && showEmptyError) showError("No MusicBrainz artist matches found.");
    return artists;
  } catch (error) {
    if (requestVersion === artistSearchVersion) showError(`Artist search failed: ${error.message}`);
    return [];
  } finally {
    if (requestVersion === artistSearchVersion) setArtistSearchPending(false);
  }
}

function prefetchArtistResolution(artist) {
  if (!artist?.mbid || artistResolutionCache.has(artist.mbid)) return;
  const promise = fetch("/api/resolve-artist", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mbid: artist.mbid }),
  }).then(async (response) => {
    if (!response.ok) throw new Error(await readErrorDetail(response));
    return response.json();
  });
  artistResolutionCache.set(artist.mbid, promise);
  promise.catch(() => artistResolutionCache.delete(artist.mbid));
}

async function resolveArtist(artist) {
  prefetchArtistResolution(artist);
  return artistResolutionCache.get(artist.mbid);
}

async function resolveArtistAndLookup(artist) {
  const startLocation = el("start-location").value.trim();
  if (!startLocation || state.loading) return;

  setLoading(true, ARTIST_RESOLVE_LOADING_MESSAGES);
  try {
    const resolved = await resolveArtist(artist);
    if (!resolved.artist_url) {
      showResolutionFailure(artist, resolved);
      return;
    }

    state.selectedArtistUrl = resolved.artist_url;
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
    state.parseStatus = data.parse_status ?? "full";
    state.externalUrl = data.external_url ?? null;
    state.provider = data.provider ?? null;
    state.selectedId = null;

    el("candidate-section").hidden = true;
    el("results-section").hidden = false;
    renderArtistHeader();
    render();
    enterResultsMode();
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
    hideArtistCandidates();
    return;
  }

  el("error").hidden = true;
  section.hidden = false;
  el("artist-url").setAttribute("aria-expanded", "true");
  activeCandidateIndex = -1;

  state.artistCandidates.forEach((artist, index) => {
    const li = document.createElement("li");
    li.className = "candidate-item";
    li.setAttribute("role", "option");
    li.id = `artist-candidate-${index}`;
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
    li.querySelector("button").addEventListener("click", () => chooseArtistCandidate(index));
    list.appendChild(li);
  });
}

async function chooseArtistCandidate(index) {
  const artist = state.artistCandidates[index];
  if (!artist) return;
  state.selectedCandidate = artist;
  el("artist-url").value = artist.name;
  hideArtistCandidates();
  updateSubmitLabel();

  if (!el("start-location").value.trim()) {
    setLocationEditor(true);
    el("start-location").focus();
    return;
  }

  await resolveArtistAndLookup(artist);
}

function hideArtistCandidates() {
  el("candidate-section").hidden = true;
  el("artist-url").setAttribute("aria-expanded", "false");
  el("artist-url").removeAttribute("aria-activedescendant");
  activeCandidateIndex = -1;
}

function setActiveCandidate(index) {
  const buttons = [...el("candidate-list").querySelectorAll(".candidate-button")];
  if (!buttons.length) return;
  activeCandidateIndex = (index + buttons.length) % buttons.length;
  buttons.forEach((button, buttonIndex) => button.classList.toggle("active", buttonIndex === activeCandidateIndex));
  el("artist-url").setAttribute("aria-activedescendant", `artist-candidate-${activeCandidateIndex}`);
}

function setArtistSearchPending(pending) {
  document.querySelector(".artist-field").classList.toggle("is-searching", pending);
  if (pending) {
    el("artist-hint").textContent = ARTIST_SEARCH_LOADING_MESSAGES[0];
  } else {
    el("artist-hint").textContent = "Start typing an artist. You can also paste a tour page URL.";
  }
}

function setSubmitLabel(label) {
  el("submit-label").textContent = label;
}

function updateSubmitLabel() {
  const query = el("artist-url").value.trim();
  if (state.selectedCandidate?.name === query || isLikelyUrl(query)) {
    setSubmitLabel("Plot the tour");
  } else {
    setSubmitLabel("Find artist");
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
  const location = el("start-location").value.trim();
  el("results-location").textContent = location ? `From ${location}` : "Upcoming route";
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

  for (const artist of artists.slice(0, 6)) {
    const li = document.createElement("li");
    li.className = "saved-chip";
    li.title = artist.url;
    li.tabIndex = 0;
    li.innerHTML = `
      <span class="chip-name">${escapeHtml(artist.name)}</span>
      <span class="chip-when">${relativeTime(artist.last_checked)}</span>
      <button class="chip-delete" type="button" aria-label="Remove ${escapeHtml(artist.name)}">✕</button>
    `;
    li.addEventListener("click", () => {
      selectSavedArtist(artist);
    });
    li.addEventListener("keydown", (event) => {
      if (event.target !== li || (event.key !== "Enter" && event.key !== " ")) return;
      event.preventDefault();
      selectSavedArtist(artist);
    });
    li.querySelector(".chip-delete").addEventListener("click", async (event) => {
      event.stopPropagation();
      await fetch(`/api/artists/${encodeURIComponent(artist.id)}`, { method: "DELETE" });
      loadSavedArtists();
    });
    list.appendChild(li);
  }
}

function selectSavedArtist(artist) {
  state.selectedCandidate = null;
  state.selectedArtistUrl = artist.url;
  el("artist-url").value = artist.name;
  hideArtistCandidates();
  updateSubmitLabel();
  const startLocation = el("start-location").value.trim();
  if (!startLocation) {
    setLocationEditor(true);
    el("start-location").focus();
    return;
  }
  lookupConcerts(artist.url, startLocation);
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
      syncLocationControl({ collapse: true });
      return;
    }
  } catch {
    /* non-fatal */
  }

  if (configStartLocation && !el("start-location").value) {
    el("start-location").value = configStartLocation;
    syncLocationControl({ collapse: true });
  }
}

function syncLocationControl({ collapse = false } = {}) {
  const location = el("start-location").value.trim();
  el("location-summary-text").textContent = location || "Set your starting point";
  if (collapse && location) setLocationEditor(false);
}

function setLocationEditor(open) {
  el("location-editor").hidden = !open;
  el("location-toggle").hidden = open;
  el("location-toggle").setAttribute("aria-expanded", open ? "true" : "false");
}

function applyInitialArtistQuery() {
  const params = new URLSearchParams(window.location.search);
  const artistQuery = params.get("artist")?.trim();
  if (artistQuery) {
    el("artist-url").value = artistQuery;
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

function updateDistanceValue() {
  const slider = el("filter-distance");
  const value = el("filter-distance-value");
  if (!slider || !value) return;
  const atMax = Number(slider.value) >= Number(slider.max);
  value.textContent = atMax ? "Any" : `${slider.value} mi`;
}

function activeFilterCount() {
  const f = state.filters;
  return [f.hideSoldOut, f.hideUnreachable, f.maxDistance != null, f.dateFrom, f.dateTo].filter(
    Boolean
  ).length;
}

function updateFilterBadge() {
  const badge = el("filter-count");
  if (!badge) return;
  const count = activeFilterCount();
  badge.textContent = String(count);
  badge.hidden = count === 0;
}

function syncDateShell(input) {
  input?.closest(".date-shell")?.classList.toggle("has-value", Boolean(input.value));
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
    updateDistanceValue();
  }
  el("filter-date-from").value = state.filters.dateFrom ?? "";
  el("filter-date-to").value = state.filters.dateTo ?? "";
  syncDateShell(el("filter-date-from"));
  syncDateShell(el("filter-date-to"));
  updateFilterBadge();
}

function clearFilters() {
  state.filters = { ...OPEN_FILTERS };
  syncFilterControls();
  render();
}

function setFilterPanelOpen(open) {
  const panel = el("filter-panel");
  const disclosure = el("filter-disclosure");
  if (!panel || !disclosure) return;
  panel.hidden = !open;
  disclosure.setAttribute("aria-expanded", open ? "true" : "false");
  disclosure.classList.toggle("open", open);
}

function initFilterControls() {
  el("filter-disclosure")?.addEventListener("click", () => {
    setFilterPanelOpen(el("filter-panel").hidden);
  });

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
    updateDistanceValue();
    render();
  });

  const from = el("filter-date-from");
  const to = el("filter-date-to");
  const today = new Date().toISOString().slice(0, 10);
  if (from) from.min = today;
  if (to) to.min = today;
  from?.addEventListener("change", () => {
    state.filters.dateFrom = from.value || null;
    syncDateShell(from);
    render();
  });
  to?.addEventListener("change", () => {
    state.filters.dateTo = to.value || null;
    syncDateShell(to);
    render();
  });

  updateDistanceValue();
  updateFilterBadge();
}

try {
  initMap();
} catch (err) {
  // Leaflet is loaded from a CDN; if it fails to load (offline, blocked, etc.)
  // don't let a broken map abort the rest of the UI setup.
  console.error("Map failed to initialize:", err);
}
initSortControls();
initFilterControls();
el("sheet-handle").addEventListener("pointerdown", onSheetPointerDown);
el("sheet-handle").addEventListener("pointermove", onSheetPointerMove);
el("sheet-handle").addEventListener("pointerup", onSheetPointerUp);
el("sheet-handle").addEventListener("pointercancel", onSheetPointerUp);
el("sheet-handle").addEventListener("click", cycleSheetPosition);
el("map-back").addEventListener("click", exitResultsMode);
map?.on("click", () => {
  if (
    mobileQuery.matches &&
    document.body.classList.contains("has-results") &&
    state.sheetPosition !== "map"
  ) {
    setSheetPosition("map", { fit: false });
  }
});
window.addEventListener("resize", () => {
  if (!document.body.classList.contains("has-results")) return;
  el("results-section").style.removeProperty("height");
  const content = document.querySelector(".results-content");
  content.inert = mobileQuery.matches && state.sheetPosition === "map";
  content.setAttribute("aria-hidden", content.inert ? "true" : "false");
  refreshMapLayout();
});
el("search-form").addEventListener("submit", (event) => {
  event.preventDefault();
  search();
});
el("artist-url").addEventListener("input", () => {
  clearTimeout(artistSearchTimer);
  state.selectedCandidate = null;
  state.selectedArtistUrl = null;
  state.artistCandidates = [];
  hideArtistCandidates();
  updateSubmitLabel();
  el("error").hidden = true;
  const query = el("artist-url").value.trim();
  if (query.length >= 2 && !isLikelyUrl(query)) {
    artistSearchTimer = setTimeout(() => searchArtistCandidates(query), 450);
  }
});
el("artist-url").addEventListener("keydown", (event) => {
  if (el("candidate-section").hidden) return;
  if (event.key === "ArrowDown") {
    event.preventDefault();
    setActiveCandidate(activeCandidateIndex + 1);
  } else if (event.key === "ArrowUp") {
    event.preventDefault();
    setActiveCandidate(activeCandidateIndex - 1);
  } else if (event.key === "Enter" && activeCandidateIndex >= 0) {
    event.preventDefault();
    chooseArtistCandidate(activeCandidateIndex);
  } else if (event.key === "Escape") {
    hideArtistCandidates();
  }
});
el("location-toggle").addEventListener("click", () => {
  const open = el("location-toggle").getAttribute("aria-expanded") !== "true";
  setLocationEditor(open);
  if (open) el("start-location").focus();
});
el("start-location").addEventListener("input", () => syncLocationControl());
el("start-location").addEventListener("change", () => syncLocationControl({ collapse: true }));
el("start-location").addEventListener("blur", () => syncLocationControl({ collapse: true }));
document.addEventListener("click", (event) => {
  if (!event.target.closest(".artist-field")) hideArtistCandidates();
});
applyInitialArtistQuery();
loadDefaults();
loadSavedArtists();
