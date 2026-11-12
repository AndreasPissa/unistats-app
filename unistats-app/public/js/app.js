import * as charts from "./charts.js";
import { institutionText } from "./institutions.js";
import { showCampusMap } from "./map.js";
import { showAssistant } from "./ai.js";

const $ = (id) => document.getElementById(id);
const format = (n) =>
  n == null
    ? "—"
    : Intl.NumberFormat("en", { maximumFractionDigits: 1 }).format(n);
const yearLabel = (value) => value?.replace("AY", "").replace("_", "–") || "—";
const ui = {
  students: {
    title: "Undergraduates",
    ranking: "Enrollment by institution",
    distribution: "By study year",
  },
  postgraduates: {
    title: "Postgraduates",
    ranking: "Postgraduate populations",
    distribution: "By study level",
  },
  graduates: {
    title: "Graduates",
    ranking: "Graduates by institution",
    distribution: "By degree grade",
  },
  staff: {
    title: "Teaching staff",
    ranking: "Staff by institution",
    distribution: "By staff category",
  },
  entry: {
    title: "Student entry",
    ranking: "First-year enrollment",
    distribution: "By study year",
  },
  catalogue: { title: "Query catalogue" },
  map: { title: "Campus map" },
  ai: { title: "Ask UniStats" },
};
const defaultState = {
  view: "students",
  source: "auto",
  year: "",
  university: "",
  department: "",
  region: "",
  city: "",
  gender: "",
  level: "",
  category: "",
  staff_group: "",
  birth_year: "",
  category_detail: "broad",
  metric: "total",
  group_by: "department",
  top: 10,
  staff: "staff",
};
const state = { ...defaultState };
for (const [k, v] of new URLSearchParams(location.search))
  if (k in state)
    state[k] = k === "top" ? Math.min(500, Math.max(3, Number(v) || 10)) : v;
if (!ui[state.view]) state.view = "students";
if (!["auto", "live", "snapshot"].includes(state.source)) state.source = "auto";
let metadata,
  queries = [],
  currentData,
  currentQuery,
  queryResult;
let abortExplore,
  abortQuery,
  exploreGeneration = 0,
  queryGeneration = 0;
let tablePage = 0,
  queryPage = 0,
  display = "chart",
  toastTimer;
const PAGE_SIZE = 12;

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) {
    const message =
      typeof data.detail === "string" ? data.detail : data.detail?.[0]?.msg;
    throw new Error(
      message || "The request could not be completed. Please try again.",
    );
  }
  return data;
}
function error(message) {
  $("error-message").textContent = message;
  $("error-banner").hidden = false;
}
function clearError() {
  $("error-banner").hidden = true;
}
function toast(message) {
  clearTimeout(toastTimer);
  $("toast").textContent = message;
  $("toast").hidden = false;
  toastTimer = setTimeout(() => ($("toast").hidden = true), 3000);
}
function option(value, label) {
  const o = document.createElement("option");
  o.value = value;
  o.textContent = label;
  return o;
}
function fill(id, values, empty, selected = "") {
  const el = $(id);
  el.replaceChildren();
  if (empty !== null) el.append(option("", empty));
  for (const [value, label] of values) el.append(option(value, label));
  el.value = selected;
  if (el.selectedIndex < 0 && el.options.length) el.selectedIndex = 0;
}
function uniqueOptions(key, labelKey) {
  return [
    ...new Map(
      metadata.departments.map((d) => [d[key], d[labelKey]]),
    ).entries(),
  ].sort((a, b) => a[1].localeCompare(b[1]));
}
function label(value) {
  if (value == null) return "—";
  const text = String(value);
  if (metadata?.institutions?.[text]?.kind === "school")
    return metadata.institutions[text].label;
  for (const d of metadata?.departments || []) {
    if (d.uri === text) return `${d.label} · ${d.universityLabel}`;
    if (d.university === text) return d.universityLabel;
    if (d.city === text) return d.cityLabel;
    if (d.region === text) return d.regionLabel;
    const program = d.programs.find((p) => p.uri === text);
    if (program) return `${d.label} · ${program.type}`;
  }
  const concept = Object.values(metadata?.concepts || {}).find(
    (c) => c.uri === text || c.id === text,
  );
  if (concept) return concept.label;
  if (text === "AGNOSTO") return "Unknown birth year";
  if (/^\d{4} KAI PRIN$/.test(text))
    return text.replace(" KAI PRIN", " or earlier");
  const end = text.split("/").pop();
  if (/^AY\d{4}_\d{4}$/.test(end)) return yearLabel(end);
  const names = {
    BeyondNormalYears: "Beyond normal duration",
    Good: "Good",
    VeryGood: "Very good",
    Excellent: "Excellent",
    MSc: "MSc",
    PhD: "PhD",
    BSc: "BSc",
    EDIP: "Laboratory teaching staff",
    EEP: "Educational staff / fellows",
    PD407: "Contract teaching staff",
  };
  if (names[end]) return names[end];
  if (/^StudyYear\d+$/.test(end)) return `Year ${end.replace("StudyYear", "")}`;
  return end.replace(/([a-z])([A-Z])/g, "$1 $2").replaceAll("_", " ");
}
function updateURL() {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(state))
    if (value !== "" && value !== defaultState[key]) params.set(key, value);
  history.replaceState(
    null,
    "",
    location.pathname + (params.size ? "?" + params : ""),
  );
}
function setSource(source) {
  const pill = $("source-pill");
  pill.textContent =
    source.mode === "live"
      ? source.cached
        ? "Live \u00b7 cached"
        : "Live database"
      : "Local RDF snapshot";
  pill.dataset.mode = source.mode;
  pill.title =
    source.mode === "snapshot"
      ? "RDF snapshot prepared 8 October 2026"
      : `Retrieved ${new Date(source.retrievedAt).toLocaleString()}`;
  $("source-message").hidden = !source.reason;
  $("source-message").textContent =
    "Live connection unavailable. Showing the RDF snapshot prepared 8 October 2026.";
}
function clearSource() {
  $("source-pill").textContent = "Ready to query";
  $("source-pill").removeAttribute("data-mode");
  $("source-pill").removeAttribute("title");
  $("source-message").hidden = true;
}
function dependentDepartments() {
  const available = metadata.departments.filter(
    (d) =>
      (!state.university || d.university === state.university) &&
      (!state.region || d.region === state.region) &&
      (!state.city || d.city === state.city),
  );
  if (!available.some((d) => d.uri === state.department)) state.department = "";
  fill(
    "department-filter",
    available.map((d) => [d.uri, `${d.label} · ${d.universityLabel}`]),
    "All departments",
    state.department,
  );
}
function dataset() {
  return state.view === "staff" ? state.staff : state.view;
}

async function navigate(view) {
  abortExplore?.abort();
  abortQuery?.abort();
  exploreGeneration++;
  queryGeneration++;
  clearError();
  const changed = state.view !== view;
  state.view = view;
  if (changed) {
    state.category = "";
    state.staff_group = "";
    state.birth_year = "";
    state.level = "";
    state.metric = "total";
    state.year = "";
  }
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === view);
    if (button.dataset.view === view)
      button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  const texts = ui[view];
  $("page-title").textContent = texts.title;
  $("explorer-view").hidden = ["catalogue", "ai", "map"].includes(view);
  $("catalogue-view").hidden = view !== "catalogue";
  $("ai-view").hidden = view !== "ai";
  $("map-view").hidden = view !== "map";
  document.querySelector(".connection").hidden = ["map", "ai"].includes(view);
  $("reset-button").hidden = ["catalogue", "ai", "map"].includes(view);
  if (view === "map") {
    clearSource();
    showCampusMap(metadata, (selection) => {
      Object.assign(state, defaultState, {
        source: state.source,
        view: "map",
        ...selection,
      });
      syncFilters();
      navigate("students");
    });
  } else if (view === "catalogue") {
    renderQueryList();
    if (!currentQuery) selectQuery(queries.find((q) => q.id === "04_sai"));
  } else if (view === "ai") {
    clearSource();
    showAssistant({ metadata, label, api, toast });
  } else {
    $("ranking-title").textContent = texts.ranking;
    $("distribution-title").textContent = texts.distribution;
    $("level-control").hidden = view !== "postgraduates";
    $("staff-control").hidden = view !== "staff";
    $("staff_group-control").hidden = view !== "staff";
    $("category-detail-control").hidden = view !== "staff";
    $("birth_year-control").hidden = view === "staff";
    $("category-control").hidden = view === "postgraduates" || view === "entry";
    $("category-control").firstChild.textContent =
      view === "graduates"
        ? "Degree grade"
        : view === "staff"
          ? "Specific staff category"
          : "Study year";
    const config = metadata.datasets.find((d) => d.id === dataset());
    if (!config.metrics.includes(state.metric)) state.metric = "total";
    fill(
      "metric-filter",
      config.metrics.map((m) => [m, metadata.metrics[m]]),
      null,
      state.metric,
    );
    $("category-filter").value = "";
    $("level-filter").value = state.level;
    $("year-filter").replaceChildren(option("", "Latest available"));
    await loadExplorer();
  }
  updateURL();
}

async function loadExplorer(refresh = false) {
  if (["catalogue", "ai", "map"].includes(state.view)) return;
  abortExplore?.abort();
  abortExplore = new AbortController();
  const generation = ++exploreGeneration;
  $("loading-line").classList.add("busy");
  $("explorer-view").setAttribute("aria-busy", "true");
  clearError();
  $("explorer-results").inert = true;
  $("explorer-results").classList.add("results-pending");
  const params = new URLSearchParams({
    dataset: dataset(),
    source: state.source,
    metric: state.metric,
    group_by: state.group_by,
    top: state.top,
    category_detail: state.category_detail,
    refresh: String(refresh),
  });
  for (const key of [
    "year",
    "university",
    "department",
    "region",
    "city",
    "gender",
    "level",
    "category",
    "staff_group",
    "birth_year",
  ])
    if (state[key]) params.set(key, state[key]);
  try {
    const result = await api("/api/explore?" + params, {
      signal: abortExplore.signal,
    });
    if (generation !== exploreGeneration) return;
    currentData = result;
    applyMetadata(result.metadata);
    state.year = result.year;
    tablePage = 0;
    $("explorer-results").hidden = false;
    setSource(result.source);
    renderExplorer(result);
    updateURL();
    return true;
  } catch (e) {
    if (e.name !== "AbortError" && generation === exploreGeneration) {
      error(e.message);
      currentData = null;
      $("explorer-results").hidden = true;
      $("source-pill").textContent = "Request failed";
      $("source-pill").removeAttribute("data-mode");
      $("source-message").hidden = true;
    }
    return false;
  } finally {
    if (generation === exploreGeneration) {
      $("explorer-results").inert = false;
      $("explorer-results").classList.remove("results-pending");
      $("loading-line").classList.remove("busy");
      $("explorer-view").setAttribute("aria-busy", "false");
    }
  }
}
function renderExplorer(data) {
  const graduateBirth = state.view === "graduates" && Boolean(state.birth_year);
  $("category-control").hidden =
    ["postgraduates", "entry"].includes(state.view) || graduateBirth;
  const metrics = metadata.datasets
    .find((d) => d.id === dataset())
    .metrics.filter((m) => !graduateBirth || m !== "excellentShare");
  fill(
    "metric-filter",
    metrics.map((m) => [m, metadata.metrics[m]]),
    null,
    state.metric,
  );
  fill(
    "staff_group-filter",
    (data.availableStaffGroups || []).map((g) => [g.id, g.label]),
    "All staff groups",
    state.staff_group,
  );
  fill(
    "birth_year-filter",
    (data.availableBirthYears || []).map((v) => [v, label(v)]),
    "All birth years",
    state.birth_year,
  );
  $("birth_year-control").hidden = !(data.availableBirthYears || []).length;
  $("category-detail-filter").value = state.category_detail;
  if (state.view === "staff")
    $("distribution-title").textContent =
      state.category_detail === "broad"
        ? "By broad staff category"
        : "By specific staff category";
  else
    $("distribution-title").textContent = graduateBirth
      ? "By gender"
      : ui[state.view].distribution;
  $("top-filter").max = Math.max(10, Math.min(500, data.allRows.length));
  fill(
    "year-filter",
    data.availableYears.map((y) => [y, yearLabel(y)]),
    null,
    state.year,
  );
  fill(
    "category-filter",
    data.availableCategories.map((c) => [c, label(c)]),
    state.view === "students"
      ? "All study years"
      : state.view === "graduates"
        ? "All grades"
        : "All categories",
    state.category,
  );
  $("year-slider").max = Math.max(0, data.availableYears.length - 1);
  $("year-slider").value = data.availableYears.indexOf(state.year);
  $("year-slider").setAttribute("aria-valuetext", yearLabel(state.year));
  $("timeline-year").textContent = yearLabel(state.year);
  $("timeline-range").textContent =
    `${yearLabel(data.availableYears[0])} to ${yearLabel(data.availableYears.at(-1))}`;
  const s = data.summary,
    empty = s.observedGroups === 0;
  $("stat-total").textContent = empty ? "—" : format(s.total);
  $("population-label").textContent =
    state.view === "graduates"
      ? "Graduates"
      : state.view === "staff"
        ? "Teaching staff"
        : state.view === "entry"
          ? "First-year students"
          : "Enrolled students";
  $("stat-total-note").textContent =
    `${yearLabel(data.year)} · matching observations`;
  $("stat-female").textContent =
    s.femaleShare == null ? "—" : format(s.femaleShare) + "%";
  $("stat-female-note").textContent = empty
    ? "No matching observations"
    : `${format(s.female)} female ${data.unit}`;
  $("stat-departments").textContent = empty ? "—" : format(s.departments);
  $("stat-department-note").textContent = "With matching observations";
  const third =
    state.view === "students"
      ? "beyondShare"
      : state.view === "graduates" && !state.birth_year
        ? "excellentShare"
        : "male";
  $("third-label").textContent =
    third === "beyondShare"
      ? "Beyond normal duration"
      : third === "excellentShare"
        ? "Excellent degree share"
        : "Male population";
  $("stat-third").textContent = empty
    ? "—"
    : format(s[third]) + (third === "male" ? "" : "%");
  $("stat-third-note").textContent =
    third === "beyondShare"
      ? `${format(s.beyond)} students in N+`
      : third === "excellentShare"
        ? "Share of observed graduates"
        : `Within the selected ${data.unit}`;
  $("dataset-note").textContent = data.note;
  $("trend-badge").textContent = data.metricLabel;
  $("empty-state").hidden = !empty;
  $("download-chart").disabled = empty || display !== "chart";
  $("ranking-chart-view").hidden = empty || display !== "chart";
  $("ranking-table-view").hidden = empty || display !== "table";
  $("chart-hint").textContent = `Select a bar to filter by ${data.groupBy}.`;
  charts.ranking(data, drillDown);
  charts.trend(data, setYear);
  charts.distribution(data, label, selectCategory);
  const legend = $("distribution-legend");
  legend.replaceChildren();
  data.distribution.forEach((r, i) => {
    const li = document.createElement("li"),
      button = document.createElement("button");
    button.className = "legend-button";
    const swatch = document.createElement("span");
    swatch.className = "legend-swatch";
    swatch.style.background = charts.palette[i % charts.palette.length];
    const name = document.createElement("span");
    name.textContent = label(r.id);
    const count = document.createElement("span");
    count.className = "legend-count";
    count.textContent = format(r.count);
    button.append(swatch, name, count);
    button.addEventListener("click", () => selectCategory(r.id));
    li.append(button);
    legend.append(li);
  });
  const trends = $("trend-values");
  trends.replaceChildren();
  data.trend.forEach((r) => {
    const span = document.createElement("span");
    span.textContent = `${yearLabel(r.year)}: ${r.value == null ? "No observations" : format(r.value) + (data.metric === "total" ? "" : "%")}`;
    trends.append(span);
  });
  renderChips();
  renderTable();
}
function setYear(year) {
  if (currentData?.availableYears.includes(year)) {
    state.year = year;
    loadExplorer();
  }
}
function drillDown(row) {
  if (state.group_by === "department") state.department = row.id;
  else if (state.group_by === "university") state.university = row.id;
  else if (state.group_by === "region") state.region = row.id;
  else if (state.group_by === "city") state.city = row.id;
  syncFilters();
  loadExplorer();
}
function selectCategory(category) {
  if (state.view === "graduates" && state.birth_year) {
    state.gender = state.gender === category ? "" : category;
    syncFilters();
    loadExplorer();
    return;
  }
  if (state.view === "staff" && state.category_detail === "broad") {
    if (metadata.staffGroups.some((g) => g.id === category)) {
      state.staff_group = state.staff_group === category ? "" : category;
      state.category = "";
    } else state.category = state.category === category ? "" : category;
    loadExplorer();
    return;
  }
  if (state.view === "postgraduates") {
    state.level = state.level === category ? "" : category;
    $("level-filter").value = state.level;
  } else if (state.view === "entry") return;
  else state.category = state.category === category ? "" : category;
  loadExplorer();
}
function syncFilters() {
  for (const key of [
    "university",
    "region",
    "gender",
    "level",
    "metric",
    "staff_group",
    "birth_year",
  ])
    $(key + "-filter").value = state[key];
  $("group-filter").value = state.group_by;
  $("top-filter").value = state.top;
  $("top-output").textContent = state.top;
  $("staff-filter").value = state.staff;
  dependentDepartments();
}
function renderChips() {
  const container = $("active-filters");
  container.replaceChildren();
  for (const key of [
    "university",
    "department",
    "region",
    "city",
    "gender",
    "level",
    "category",
    "staff_group",
    "birth_year",
  ]) {
    if (!state[key]) continue;
    const chip = document.createElement("span");
    chip.className = "filter-chip";
    const text = institutionText(metadata, state[key], label(state[key]));
    const button = document.createElement("button");
    button.textContent = "×";
    button.setAttribute("aria-label", `Remove ${key} filter`);
    button.addEventListener("click", () => {
      state[key] = "";
      syncFilters();
      loadExplorer();
    });
    chip.append(text, button);
    container.append(chip);
  }
}
function renderTable() {
  if (!currentData) return;
  const search = $("table-search").value.toLowerCase();
  const rows = currentData.allRows.filter((r) =>
    (r.label + " " + r.university + " " + r.city)
      .toLowerCase()
      .includes(search),
  );
  const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  tablePage = Math.min(tablePage, pages - 1);
  const table = $("results-table"),
    head = document.createElement("tr");
  for (const name of [
    state.group_by[0].toUpperCase() + state.group_by.slice(1),
    "Population",
    "Female share",
    "Selected measure",
  ]) {
    const th = document.createElement("th");
    th.textContent = name;
    if (head.children.length) th.className = "number";
    head.append(th);
  }
  table.tHead.replaceChildren(head);
  table.tBodies[0].replaceChildren();
  for (const row of rows.slice(
    tablePage * PAGE_SIZE,
    (tablePage + 1) * PAGE_SIZE,
  )) {
    const tr = document.createElement("tr"),
      first = document.createElement("td"),
      button = document.createElement("button"),
      identity = document.createElement("div");
    identity.className = "institution-identity";
    button.className = "row-link row-filter";
    button.textContent = "Filter";
    button.setAttribute("aria-label", `Filter by ${row.label}`);
    button.addEventListener("click", () => drillDown(row));
    identity.append(institutionText(metadata, row.id, row.label), button);
    const sub = document.createElement("span");
    sub.className = "cell-subtitle";
    const department = metadata.departments.find((d) => d.uri === row.id);
    sub.append(
      institutionText(metadata, department?.university, row.university),
    );
    first.append(identity, sub);
    tr.append(first);
    for (const v of [
      format(row.total),
      format(row.femaleShare) + "%",
      format(row.value) + (state.metric === "total" ? "" : "%"),
    ]) {
      const td = document.createElement("td");
      td.className = "number";
      td.textContent = v;
      tr.append(td);
    }
    table.tBodies[0].append(tr);
  }
  $("row-count").textContent = `${format(rows.length)} groups`;
  $("page-count").textContent = `${tablePage + 1} / ${pages}`;
  $("previous-page").disabled = tablePage === 0;
  $("next-page").disabled = tablePage >= pages - 1;
}
function csv(filename, columns, rows) {
  const safe = (value) => {
    let s = String(value ?? "");
    if (typeof value === "string" && /^[=+\-@\t\r]/.test(s)) s = "'" + s;
    return '"' + s.replaceAll('"', '""') + '"';
  };
  const text = [
    columns.map(safe).join(","),
    ...rows.map((row) => columns.map((c) => safe(row[c])).join(",")),
  ].join("\r\n");
  const url = URL.createObjectURL(
    new Blob(["\ufeff" + text], { type: "text/csv;charset=utf-8" }),
  );
  download(url, filename);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function download(url, filename) {
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
}

function renderQueryList() {
  const search = $("query-search").value.toLowerCase(),
    list = $("query-list");
  list.replaceChildren();
  let group = "";
  for (const q of queries.filter((q) =>
    (q.title + " " + q.id + " " + q.group).toLowerCase().includes(search),
  )) {
    if (q.group !== group) {
      group = q.group;
      const heading = document.createElement("p");
      heading.className = "query-group";
      heading.textContent = group;
      list.append(heading);
    }
    const button = document.createElement("button");
    button.className =
      "query-option" + (currentQuery?.id === q.id ? " selected" : "");
    button.textContent = q.title;
    const small = document.createElement("small");
    small.textContent = q.id;
    button.append(small);
    button.addEventListener("click", () => selectQuery(q));
    list.append(button);
  }
  if (!list.children.length) {
    const p = document.createElement("p");
    p.textContent = "No questions match this search.";
    list.append(p);
  }
}
function selectQuery(q) {
  abortQuery?.abort();
  queryGeneration++;
  currentQuery = q;
  queryResult = null;
  clearSource();
  $("query-results").hidden = true;
  $("run-query").disabled = false;
  $("query-run-status").textContent = "Choose filters, then run the query.";
  $("query-id").textContent = q.id;
  $("query-title").textContent = q.title;
  $("catalogue-code").textContent = q.query;
  $("query-guidance").textContent = q.description;
  const form = $("query-filters");
  form.replaceChildren();
  const names = {
    academicYear: "Academic year",
    department: "Department",
    gender: "Gender",
    programType: "Study level",
  };
  for (const field of q.filters) {
    const wrapper = document.createElement("label");
    wrapper.textContent = names[field];
    const select = document.createElement("select");
    select.name = field;
    select.append(
      option("", field === "programType" ? "MSc & PhD" : "All available"),
    );
    let values = [];
    if (field === "academicYear")
      values = metadata.availableYears.map((y) => [y, yearLabel(y)]);
    if (field === "department")
      values = metadata.departments.map((d) => [
        d.uri,
        `${d.label} · ${d.universityLabel}`,
      ]);
    if (field === "gender")
      values = [
        ["Female", "Female"],
        ["Male", "Male"],
      ];
    if (field === "programType")
      values = [
        ["MSc", "MSc"],
        ["PhD", "PhD"],
      ];
    values.forEach(([v, l]) => select.append(option(v, l)));
    if (field === "academicYear")
      select.value = metadata.availableYears.at(-1) || "";
    wrapper.append(select);
    form.append(wrapper);
  }
  renderQueryList();
}
async function runQuery() {
  if (!currentQuery) return;
  abortQuery?.abort();
  abortQuery = new AbortController();
  const generation = ++queryGeneration;
  clearError();
  $("run-query").disabled = true;
  $("query-run-status").textContent = "Running query…";
  $("query-results").hidden = true;
  const filters = Object.fromEntries(
    [...new FormData($("query-filters"))].filter(([, v]) => v),
  );
  try {
    const result = await api(`/api/catalogue/${currentQuery.id}/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filters, source: state.source }),
      signal: abortQuery.signal,
    });
    if (generation !== queryGeneration) return;
    queryResult = result;
    queryPage = 0;
    setSource(result.source);
    $("catalogue-code").textContent = result.query;
    $("query-run-status").textContent =
      `${format(result.rows.length)} result rows · ${result.source.mode === "live" ? "live database" : "local snapshot"}`;
    $("query-results").hidden = false;
    const numbers = result.columns.filter(
      (key) =>
        result.rows.some((r) => r[key] != null) &&
        result.rows.every(
          (r) => r[key] == null || Number.isFinite(Number(r[key])),
        ),
    );
    const dimensions = result.columns.filter((key) => !numbers.includes(key));
    const preferred = [
      "SAIrel",
      "studentsPerDEP",
      "studentsPerStaff",
      "SOTGR",
      "deltaOTGR",
      "GAPRI",
      "PGR",
      "DII",
      "GPR",
      "externalStaffShare",
      "femalePercentage",
      "averageAge",
      "averageGraduateAge",
    ];
    fill(
      "query-value",
      numbers.map((k) => [k, label(k)]),
      null,
      preferred.find((k) => numbers.includes(k)) || numbers.at(-1),
    );
    fill(
      "query-dimension",
      dimensions.map((k) => [k, label(k)]),
      null,
      ["department", "program", "city", "region", "academicYear"].find((k) =>
        dimensions.includes(k),
      ) || dimensions[0],
    );
    const chartable = !!numbers.length && result.rows.length > 0;
    document.querySelector(".query-chart-controls").hidden = !chartable;
    document.querySelector(".query-chart-frame").hidden = !chartable;
    $("query-chart-hint").hidden = !chartable;
    if (chartable) renderQueryChart();
    else charts.destroy("query-chart");
    renderQueryTable();
  } catch (e) {
    if (e.name !== "AbortError" && generation === queryGeneration) {
      error(e.message);
      $("query-run-status").textContent =
        "Query failed. No result has been substituted.";
    }
  } finally {
    if (generation === queryGeneration) $("run-query").disabled = false;
  }
}
function renderQueryChart() {
  if (queryResult)
    charts.catalogue(
      queryResult.rows,
      $("query-dimension").value,
      $("query-value").value,
      label,
    );
}
function renderQueryTable() {
  const { columns, rows } = queryResult,
    table = $("query-table"),
    head = document.createElement("tr");
  columns.forEach((key) => {
    const th = document.createElement("th");
    th.textContent = label(key);
    head.append(th);
  });
  table.tHead.replaceChildren(head);
  table.tBodies[0].replaceChildren();
  for (const row of rows.slice(
    queryPage * PAGE_SIZE,
    (queryPage + 1) * PAGE_SIZE,
  )) {
    const tr = document.createElement("tr");
    columns.forEach((key) => {
      const td = document.createElement("td");
      const text =
        row[key] == null
          ? "—"
          : Number.isFinite(Number(row[key]))
            ? format(Number(row[key]))
            : label(row[key]);
      const entity =
        {
          deptLabelEn: row.department,
          deptLabelEl: row.department,
          uniLabelEn: row.university,
          uniLabelEl: row.university,
          schoolLabelEn: row.school,
          schoolLabelEl: row.school,
        }[key] || row[key];
      td.append(institutionText(metadata, entity, text));
      td.title = String(row[key] ?? "");
      tr.append(td);
    });
    table.tBodies[0].append(tr);
  }
  const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  $("query-row-count").textContent = rows.length
    ? `${format(rows.length)} rows. CSV retains exact values.`
    : "No matching observations. Try broader filters.";
  $("query-page-count").textContent = `${queryPage + 1} / ${pages}`;
  $("query-previous").disabled = queryPage === 0;
  $("query-next").disabled = queryPage >= pages - 1;
}

function wire() {
  $("navigation").addEventListener("click", (e) => {
    const button = e.target.closest("[data-view]");
    if (button) navigate(button.dataset.view);
  });
  $("source-select").value = state.source;
  $("source-select").addEventListener("change", async () => {
    state.source = $("source-select").value;
    updateURL();
    try {
      applyMetadata(await api("/api/metadata?source=" + state.source));
    } catch (e) {
      error(e.message);
      return;
    }
    if (state.view === "catalogue") {
      abortQuery?.abort();
      queryGeneration++;
      $("run-query").disabled = false;
      queryResult = null;
      $("query-results").hidden = true;
      clearSource();
      if (currentQuery) selectQuery(currentQuery);
      $("query-run-status").textContent =
        "Source changed. Run the query to fetch new results.";
    } else loadExplorer();
  });
  $("filter-form").addEventListener("submit", (e) => e.preventDefault());
  $("query-filters").addEventListener("submit", (e) => {
    e.preventDefault();
    runQuery();
  });
  for (const key of [
    "year",
    "university",
    "department",
    "region",
    "gender",
    "level",
    "category",
    "staff_group",
    "birth_year",
  ])
    $(key + "-filter").addEventListener("change", () => {
      state[key] = $(key + "-filter").value;
      if (key === "staff_group") state.category = "";
      if (key === "birth_year" && state.view === "graduates") {
        state.category = "";
        if (state.metric === "excellentShare") state.metric = "total";
      }
      if (["university", "region"].includes(key)) dependentDepartments();
      loadExplorer();
    });
  $("staff-filter").addEventListener("change", () => {
    state.staff = $("staff-filter").value;
    state.staff_group = "";
    state.year = "";
    state.category = "";
    loadExplorer();
  });
  $("category-detail-filter").addEventListener("change", () => {
    state.category_detail = $("category-detail-filter").value;
    loadExplorer();
  });
  $("refresh-data").addEventListener("click", async () => {
    const button = $("refresh-data");
    button.disabled = true;
    try {
      applyMetadata(
        await api(
          `/api/metadata?source=${state.view === "ai" ? "live" : state.source}&refresh=true`,
        ),
      );
      if (state.view === "map")
        showCampusMap(metadata, (selection) => {
          Object.assign(state, selection);
          navigate("students");
        });
      else if (state.view === "catalogue") {
        if (currentQuery) selectQuery(currentQuery);
      } else if (state.view === "ai")
        showAssistant({ metadata, label, api, toast });
      else await loadExplorer(true);
      toast("Data and available filters refreshed.");
    } catch (e) {
      error(e.message);
    } finally {
      button.disabled = false;
    }
  });
  $("metric-filter").addEventListener("change", () => {
    state.metric = $("metric-filter").value;
    loadExplorer();
  });
  $("group-filter").addEventListener("change", () => {
    state.group_by = $("group-filter").value;
    loadExplorer();
  });
  let timer;
  $("top-filter").addEventListener("input", () => {
    $("top-output").textContent = $("top-filter").value;
    clearTimeout(timer);
    timer = setTimeout(() => {
      state.top = Number($("top-filter").value);
      loadExplorer();
    }, 180);
  });
  $("year-slider").addEventListener("input", () => {
    if (!currentData) return;
    $("timeline-year").textContent = yearLabel(
      currentData.availableYears[Number($("year-slider").value)],
    );
  });
  $("year-slider").addEventListener(
    "change",
    () =>
      currentData &&
      setYear(currentData.availableYears[Number($("year-slider").value)]),
  );
  $("reset-button").addEventListener("click", () => {
    const view = state.view,
      source = state.source;
    Object.assign(state, defaultState, { view, source });
    syncFilters();
    navigate(view);
  });
  $("retry-button").addEventListener("click", () =>
    !metadata
      ? location.reload()
      : state.view === "catalogue"
        ? runQuery()
        : loadExplorer(),
  );
  document.querySelectorAll("[data-display]").forEach((button) =>
    button.addEventListener("click", () => {
      display = button.dataset.display;
      document.querySelectorAll("[data-display]").forEach((b) => {
        b.classList.toggle("selected", b === button);
        b.setAttribute("aria-pressed", b === button);
      });
      if (currentData) renderExplorer(currentData);
    }),
  );
  $("table-search").addEventListener("input", () => {
    tablePage = 0;
    renderTable();
  });
  $("previous-page").addEventListener("click", () => {
    tablePage--;
    renderTable();
  });
  $("next-page").addEventListener("click", () => {
    tablePage++;
    renderTable();
  });
  $("export-csv").addEventListener("click", () => {
    if (currentData)
      csv(
        `unistats-${dataset()}-${state.year}-${currentData.source.mode}.csv`,
        [
          "label",
          "university",
          "city",
          "total",
          "female",
          "male",
          "femaleShare",
          "beyondShare",
          "excellentShare",
          "value",
        ],
        currentData.allRows,
      );
  });
  $("download-chart").addEventListener("click", () => {
    const url = charts.image("ranking-chart");
    if (url)
      download(url, `unistats-${dataset()}-${state.year}-${state.metric}.png`);
  });
  $("guide-button").addEventListener("click", () =>
    $("guide-dialog").showModal(),
  );
  document
    .querySelectorAll("[data-close]")
    .forEach((button) =>
      button.addEventListener("click", () => button.closest("dialog").close()),
    );
  $("view-query").addEventListener("click", () => {
    if (!currentData) return;
    $("explorer-code").textContent = currentData.query;
    $("query-explanation").textContent = currentData.filtering;
    $("filter-code").textContent = JSON.stringify(
      {
        dataset: dataset(),
        year: state.year,
        university: state.university || null,
        department: state.department || null,
        region: state.region || null,
        city: state.city || null,
        gender: state.gender || null,
        level: state.level || null,
        category: state.category || null,
        staff_group: state.staff_group || null,
        birth_year: state.birth_year || null,
        metric: state.metric,
        groupBy: state.group_by,
      },
      null,
      2,
    );
    $("query-dialog").showModal();
  });
  $("copy-query").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(currentData.query);
      toast("SPARQL copied.");
    } catch {
      toast("Copy is unavailable. Select the query text to copy it.");
    }
  });
  $("query-search").addEventListener("input", renderQueryList);
  $("run-query").addEventListener("click", runQuery);
  $("query-filters").addEventListener("change", () => {
    abortQuery?.abort();
    queryGeneration++;
    $("run-query").disabled = false;
    queryResult = null;
    $("query-results").hidden = true;
    clearSource();
    $("query-run-status").textContent =
      "Filters changed. Run the query to update results.";
  });
  $("query-value").addEventListener("change", renderQueryChart);
  $("query-dimension").addEventListener("change", renderQueryChart);
  $("query-previous").addEventListener("click", () => {
    queryPage--;
    renderQueryTable();
  });
  $("query-next").addEventListener("click", () => {
    queryPage++;
    renderQueryTable();
  });
  $("query-export").addEventListener("click", () => {
    if (queryResult)
      csv(
        `unistats-${queryResult.queryId}-${queryResult.source.mode}.csv`,
        queryResult.columns,
        queryResult.rows,
      );
  });
}

function registerWebTools() {
  const context = document.modelContext;
  if (!context?.registerTool) return;
  const controller = new AbortController();
  window.addEventListener("pagehide", () => controller.abort(), { once: true });
  const tools = [
    {
      name: "read_unistats_explorer",
      title: "Read UniStats explorer",
      description:
        "Read current filters, source and summary without changing the interface.",
      inputSchema: {
        type: "object",
        properties: {},
        additionalProperties: false,
      },
      annotations: { readOnlyHint: true, untrustedContentHint: true },
      execute: () => ({
        filters: { ...state },
        summary: currentData?.summary ?? null,
        source: currentData?.source ?? null,
      }),
    },
    {
      name: "set_unistats_academic_year",
      title: "Change UniStats academic year",
      description:
        "Change the visible explorer year and wait for updated results.",
      inputSchema: {
        type: "object",
        properties: {
          year: { type: "string", pattern: "^AY20[0-9]{2}_20[0-9]{2}$" },
        },
        required: ["year"],
        additionalProperties: false,
      },
      annotations: { readOnlyHint: false, untrustedContentHint: true },
      execute: async (input) => {
        if (
          ["catalogue", "ai", "map"].includes(state.view) ||
          !currentData?.availableYears.includes(input?.year)
        )
          throw new Error("This year is unavailable in the current explorer.");
        state.year = input.year;
        if (!(await loadExplorer()))
          throw new Error("The updated results could not be loaded.");
        return {
          year: state.year,
          summary: currentData.summary,
          source: currentData.source,
        };
      },
    },
  ];
  for (const tool of tools)
    try {
      Promise.resolve(
        context.registerTool(tool, { signal: controller.signal }),
      ).catch(() => {});
    } catch {}
}

function applyMetadata(next) {
  if (!next) return;
  metadata = next;
  $("coverage-note").textContent =
    `Source: ELSTAT. Coverage: ${metadata.departments.length} departments in ${new Set(metadata.departments.map((d) => d.university)).size} universities.`;
  fill(
    "university-filter",
    uniqueOptions("university", "universityLabel"),
    "All universities",
    state.university,
  );
  fill(
    "region-filter",
    uniqueOptions("region", "regionLabel"),
    "All regions",
    state.region,
  );
  dependentDepartments();
}

async function init() {
  try {
    [metadata, { queries }] = await Promise.all([
      api("/api/metadata?source=" + state.source),
      api("/api/catalogue"),
    ]);
    if (
      !new URLSearchParams(location.search).has("source") &&
      ["auto", "live", "snapshot"].includes(metadata.defaultSource)
    )
      state.source = metadata.defaultSource;
    fill(
      "university-filter",
      uniqueOptions("university", "universityLabel"),
      "All universities",
      state.university,
    );
    fill(
      "region-filter",
      uniqueOptions("region", "regionLabel"),
      "All regions",
      state.region,
    );
    syncFilters();
    wire();
    await navigate(state.view);
    registerWebTools();
  } catch (e) {
    $("retry-button").onclick = () => location.reload();
    error("The application could not load: " + e.message);
    $("source-pill").textContent = "Connection error";
  }
}
init();
