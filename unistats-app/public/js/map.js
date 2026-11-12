import { institutionText } from "./institutions.js";
import { blueprintBasemap } from "./map-basemap.js";

const $ = (id) => document.getElementById(id);
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
let map,
  clusters,
  tiles,
  metadata,
  onExplore,
  visible = [],
  selected,
  searchTimer;
const popupOptions = {
  maxWidth: 300,
  minWidth: 220,
  className: "campus-popup-shell",
  autoPanPaddingTopLeft: [20, 76],
  autoPanPaddingBottomRight: [20, 20],
};
const placeNameOptions = {
  permanent: true,
  direction: "right",
  offset: [14, 0],
  className: "campus-place-name",
};

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function action(text, handler, className = "button primary compact") {
  const button = element("button", text, className);
  button.type = "button";
  button.addEventListener("click", handler);
  return button;
}

function popup(department) {
  const box = element("div", null, "campus-popup");
  const title = element("h3");
  title.append(institutionText(metadata, department.uri, department.label));
  const university = element("p", null, "campus-university");
  university.append(
    institutionText(
      metadata,
      department.university,
      department.universityLabel,
    ),
  );
  const place = element(
    "p",
    `${department.cityLabel} · ${department.regionLabel}`,
    "campus-place",
  );
  const coordinates = element(
    "p",
    `${department.location.latitude.toFixed(5)}, ${department.location.longitude.toFixed(5)}`,
    "campus-coordinates",
  );
  const controls = element("div", null, "campus-popup-actions");
  controls.append(
    action("Department statistics", () =>
      onExplore({
        university: department.university,
        department: department.uri,
      }),
    ),
    action(
      "University statistics",
      () => onExplore({ university: department.university }),
      "button secondary compact",
    ),
  );
  const osm = element("a", "Open in OpenStreetMap", "map-osm-link");
  const { latitude, longitude } = department.location;
  osm.href = `https://www.openstreetmap.org/?mlat=${latitude}&mlon=${longitude}#map=17/${latitude}/${longitude}`;
  osm.target = "_blank";
  osm.rel = "noopener noreferrer";
  box.append(title, university, place, coordinates, controls, osm);
  return box;
}

function select(uri) {
  selected = uri;
  document.querySelectorAll(".map-location").forEach((card) => {
    const active = card.dataset.department === uri;
    card.classList.toggle("selected", active);
    card
      .querySelector(".map-show-location")
      .setAttribute("aria-pressed", String(active));
  });
}

function showLocation(uri) {
  const department = visible.find((d) => d.uri === uri);
  if (!department) return;
  select(uri);
  const position = [
    department.location.latitude,
    department.location.longitude,
  ];
  map.setView(position, Math.max(14, map.getZoom()), {
    animate: false,
  });
  // Directory selection opens at the original coordinate even when departments
  // share a cluster. It does not depend on a marker becoming individually visible.
  window.L.popup(popupOptions)
    .setLatLng(position)
    .setContent(popup(department))
    .openOn(map);
}

function renderDirectory() {
  const list = $("map-location-list");
  list.replaceChildren();
  if (!visible.length) {
    list.append(
      element(
        "p",
        "No locations match. Try another university, region or search.",
        "map-no-results",
      ),
    );
    return;
  }
  for (const department of visible) {
    const card = element("article", null, "map-location");
    card.dataset.department = department.uri;
    const city = element("p", department.cityLabel, "campus-city");
    const heading = element("h3");
    heading.append(institutionText(metadata, department.uri, department.label));
    const university = element("p", null, "campus-university");
    university.append(
      institutionText(
        metadata,
        department.university,
        department.universityLabel,
      ),
    );
    const button = action(
      "Show on map",
      () => showLocation(department.uri),
      "text-button map-show-location",
    );
    button.setAttribute(
      "aria-label",
      `Show ${department.label}, ${department.universityLabel} on map`,
    );
    button.setAttribute("aria-pressed", String(selected === department.uri));
    card.append(city, heading, university, button);
    list.append(card);
  }
}

function fitLocations() {
  if (!visible.length) return;
  map.fitBounds(
    visible.map((d) => [d.location.latitude, d.location.longitude]),
    {
      padding: [40, 40],
      maxZoom: 15,
      animate: false,
    },
  );
}

function render() {
  clearTimeout(searchTimer);
  const search = $("map-search").value.trim().toLocaleLowerCase();
  const university = $("map-university").value,
    region = $("map-region").value;
  visible = metadata.departments.filter(
    (d) =>
      d.location &&
      (!university || d.university === university) &&
      (!region || d.region === region) &&
      (!search ||
        `${d.label} ${d.universityLabel} ${d.cityLabel} ${d.regionLabel}`
          .toLocaleLowerCase()
          .includes(search)),
  );
  selected = null;
  map.closePopup();
  clusters.clearLayers();
  for (const department of visible) {
    const dot = element("span", null, "campus-pin-core");
    const marker = window.L.marker(
      [department.location.latitude, department.location.longitude],
      {
        icon: window.L.divIcon({
          className: "campus-marker",
          html: dot,
          iconSize: [26, 26],
          iconAnchor: [13, 13],
        }),
        title: `${department.label} · ${department.universityLabel}`,
        alt: `${department.label}, ${department.cityLabel}`,
        keyboard: true,
        campusCity: department.cityLabel,
      },
    );
    marker.bindPopup(() => popup(department), popupOptions);
    marker.bindTooltip(element("span", department.cityLabel), placeNameOptions);
    marker.on("popupopen", () => select(department.uri));
    clusters.addLayer(marker);
  }
  const universityCount = new Set(visible.map((d) => d.university)).size;
  $("map-count").textContent =
    `${visible.length} ${visible.length === 1 ? "location" : "locations"} · ${universityCount} ${universityCount === 1 ? "university" : "universities"}`;
  $("map-directory-count").textContent = visible.length;
  $("map-fit").disabled = visible.length === 0;
  renderDirectory();
  fitLocations();
}

function populate(id, key, label, prompt) {
  const select = $(id);
  const previous = select.value;
  select.replaceChildren(new Option(prompt, ""));
  const options = [
    ...new Map(
      metadata.departments
        .filter((d) => d.location)
        .map((d) => [d[key], d[label]]),
    ).entries(),
  ];
  options.sort((a, b) => a[1].localeCompare(b[1]));
  for (const [value, text] of options) select.add(new Option(text, value));
  if (options.some(([value]) => value === previous)) select.value = previous;
}

export function showCampusMap(data, explore) {
  metadata = data;
  onExplore = explore;
  const rdfLocations = metadata.departments.filter(
    (d) => d.location?.source === "RDF coordinates",
  ).length;
  $("map-location-source").textContent = rdfLocations
    ? `${metadata.metadataSource} coordinates · workbook fallback`
    : "Locations from cs_schools.xlsx";
  if (!map) {
    const L = window.L;
    if (!L?.markerClusterGroup || !L?.VectorGrid?.Protobuf)
      throw new Error("The map library could not load. Refresh the page.");
    map = L.map("campus-map", {
      center: [38.3, 23.3],
      zoom: 6,
      minZoom: 5,
      maxZoom: 19,
      maxBounds: [
        [32, 16],
        [44, 31],
      ],
      maxBoundsViscosity: 0.8,
      scrollWheelZoom: false,
      zoomAnimation: !reducedMotion,
      fadeAnimation: !reducedMotion,
      markerZoomAnimation: !reducedMotion,
    });
    tiles = blueprintBasemap().addTo(map);
    L.control
      .scale({ imperial: false, maxWidth: 100, position: "bottomleft" })
      .addTo(map);
    tiles.on("tileerror", () => {
      $("map-tile-notice").hidden = false;
    });
    clusters = L.markerClusterGroup({
      maxClusterRadius: 40,
      showCoverageOnHover: false,
      animate: !reducedMotion,
      spiderfyOnMaxZoom: true,
      spiderfyDistanceMultiplier: 1.5,
      spiderLegPolylineOptions: { color: "#96c3b2", weight: 1, opacity: 0.65 },
      iconCreateFunction: (group) => {
        const cities = [
          ...new Set(
            group
              .getAllChildMarkers()
              .map((marker) => marker.options.campusCity),
          ),
        ];
        group.bindTooltip(
          element("span", cities.join(" · ")),
          placeNameOptions,
        );
        const count = element("span", group.getChildCount());
        count.setAttribute(
          "aria-label",
          `${group.getChildCount()} locations. Zoom in to explore.`,
        );
        return L.divIcon({
          className: "campus-cluster",
          html: count,
          iconSize: [38, 38],
        });
      },
    }).addTo(map);
    populate(
      "map-university",
      "university",
      "universityLabel",
      "All universities",
    );
    populate("map-region", "region", "regionLabel", "All regions");
    $("map-filters").addEventListener("submit", (event) =>
      event.preventDefault(),
    );
    $("map-search").addEventListener("input", () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(render, 150);
    });
    for (const id of ["map-university", "map-region"])
      $(id).addEventListener("change", render);
    $("map-reset").addEventListener("click", () => {
      clearTimeout(searchTimer);
      $("map-filters").reset();
      render();
    });
    $("map-fit").addEventListener("click", fitLocations);
    $("map-labels").addEventListener("click", () => {
      const enabled = $("campus-map").classList.toggle("show-place-names");
      $("map-labels").setAttribute("aria-pressed", String(enabled));
    });
    $("map-retry-tiles").addEventListener("click", () => {
      $("map-tile-notice").hidden = true;
      tiles.redraw();
    });
    window.addEventListener("unistats:themechange", () => {
      map.removeLayer(tiles);
      tiles = blueprintBasemap().addTo(map);
      tiles.on("tileerror", () => {
        $("map-tile-notice").hidden = false;
      });
    });
  }
  populate(
    "map-university",
    "university",
    "universityLabel",
    "All universities",
  );
  populate("map-region", "region", "regionLabel", "All regions");
  render();
  requestAnimationFrame(() => {
    map.invalidateSize({ pan: false });
    fitLocations();
  });
}
