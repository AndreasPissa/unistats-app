/* Locally served Chart.js. Every visualization has an equivalent text/table view. */
const charts = new Map();
const styles = getComputedStyle(document.documentElement);
const theme = Object.fromEntries(
  [
    "accent",
    "muted",
    "line-subtle",
    "surface",
    "raised",
    "ink",
    "chart-bar",
  ].map((name) => [name, styles.getPropertyValue("--" + name).trim()]),
);
export const palette = [
  "#96c3b2",
  "#709c82",
  "#b5c3a2",
  "#4c7967",
  "#7c9690",
  "#cbc7a3",
  "#526d55",
  "#8cad83",
  "#69887a",
  "#c3d9c3",
  "#416b5b",
  "#9bb49e",
  "#b9cdb0",
  "#627968",
  "#a9bda1",
];
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
const format = (n) =>
  new Intl.NumberFormat("en", { maximumFractionDigits: 1 }).format(n);

function replace(id, config) {
  charts.get(id)?.destroy();
  const canvas = document.getElementById(id);
  if (!window.Chart)
    throw new Error(
      "The chart library is missing. Run npm install in the application folder.",
    );
  const chart = new window.Chart(canvas, config);
  charts.set(id, chart);
  return chart;
}
function base() {
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: reducedMotion ? false : { duration: 320 },
    font: { family: "Segoe UI, Arial, sans-serif" },
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: theme.raised,
        titleColor: theme.ink,
        bodyColor: theme.ink,
        borderColor: theme["line-subtle"],
        borderWidth: 1,
        padding: 13,
        cornerRadius: 7,
        titleFont: { size: 12 },
        bodyFont: { size: 13 },
        displayColors: false,
      },
    },
  };
}
export function ranking(data, onSelect) {
  const percent = data.metric !== "total";
  const options = base();
  const labels = data.ranking.map((r) => r.label);
  document.querySelector(".ranking-frame").style.height =
    `${Math.max(290, data.ranking.length * 31 + 38)}px`;
  options.indexAxis = "y";
  options.scales = {
    x: {
      beginAtZero: true,
      suggestedMax: percent ? 100 : undefined,
      border: { display: false },
      grid: { color: theme["line-subtle"] },
      ticks: {
        color: theme.muted,
        font: { size: 11 },
        callback: (v) =>
          percent
            ? `${v}%`
            : Intl.NumberFormat("en", { notation: "compact" }).format(v),
      },
    },
    y: {
      border: { display: false },
      grid: { display: false },
      ticks: {
        color: theme.muted,
        font: { size: 12 },
        autoSkip: false,
        callback: (_, index) => {
          const s = labels[index];
          const max = innerWidth < 650 ? 20 : 40;
          return s?.length > max ? s.slice(0, max - 1) + "…" : s;
        },
      },
    },
  };
  options.plugins.tooltip.callbacks = {
    title: (items) => labels[items[0].dataIndex],
    label: (item) =>
      `${data.metricLabel}: ${format(item.raw)}${percent ? "%" : ""}`,
    afterLabel: (item) => data.ranking[item.dataIndex].university || "",
  };
  options.onClick = (_, elements) => {
    if (elements.length) onSelect(data.ranking[elements[0].index]);
  };
  options.onHover = (event, elements) => {
    event.native.target.style.cursor = elements.length ? "pointer" : "default";
  };
  return replace("ranking-chart", {
    type: "bar",
    data: {
      labels,
      datasets: [
        {
          data: data.ranking.map((r) => r.value),
          backgroundColor: data.ranking.map((_, i) =>
            i === 0 ? theme.accent : theme["chart-bar"],
          ),
          hoverBackgroundColor: theme.accent,
          borderRadius: 4,
          maxBarThickness: 19,
        },
      ],
    },
    options,
  });
}
export function trend(data, onYear) {
  const options = base();
  const labels = data.trend.map((r) => r.year.slice(2).replace("_", "–"));
  options.scales = {
    x: {
      border: { display: false },
      grid: { display: false },
      ticks: { color: theme.muted, maxRotation: 35, font: { size: 10 } },
    },
    y: {
      beginAtZero: true,
      border: { display: false },
      grid: { color: theme["line-subtle"] },
      ticks: {
        color: theme.muted,
        font: { size: 11 },
        maxTicksLimit: 5,
        callback: (v) =>
          data.metric === "total"
            ? Intl.NumberFormat("en", { notation: "compact" }).format(v)
            : `${v}%`,
      },
    },
  };
  options.plugins.tooltip.callbacks = {
    label: (item) =>
      `${data.metricLabel}: ${format(item.raw)}${data.metric === "total" ? "" : "%"}`,
  };
  options.onClick = (_, points) => {
    if (points.length) onYear(data.trend[points[0].index].year);
  };
  return replace("trend-chart", {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          data: data.trend.map((r) => r.value),
          borderColor: theme.accent,
          backgroundColor: "#96c3b210",
          pointBackgroundColor: theme.accent,
          pointBorderColor: theme.surface,
          pointBorderWidth: 2,
          pointRadius: data.trend.map((r) => (r.year === data.year ? 6 : 4)),
          pointHoverRadius: 7,
          borderWidth: 2,
          tension: 0.18,
          spanGaps: false,
          fill: false,
        },
      ],
    },
    options,
  });
}
export function distribution(data, label, onSelect) {
  const options = base();
  options.cutout = "72%";
  options.plugins.tooltip.callbacks = {
    label: (item) => `${format(item.raw)} ${data.unit}`,
  };
  options.onClick = (_, points) => {
    if (points.length) onSelect(data.distribution[points[0].index].id);
  };
  return replace("distribution-chart", {
    type: "doughnut",
    data: {
      labels: data.distribution.map((r) => label(r.id)),
      datasets: [
        {
          data: data.distribution.map((r) => r.count),
          backgroundColor: data.distribution.map(
            (_, i) => palette[i % palette.length],
          ),
          borderWidth: 3,
          borderColor: theme.surface,
          hoverOffset: 5,
        },
      ],
    },
    options,
  });
}
export function catalogue(
  rows,
  dimension,
  measure,
  label,
  id = "query-chart",
  unit = "",
  measureLabel = measure,
) {
  const shown = rows
    .filter((r) => r[measure] != null && Number.isFinite(Number(r[measure])))
    .slice(0, 20);
  const options = base();
  options.scales = {
    x: {
      grid: { display: false },
      border: { display: false },
      ticks: {
        color: theme.muted,
        font: { size: 10 },
        maxRotation: 45,
        callback: (_, i) => {
          const v = dimension ? label(shown[i][dimension]) : `Row ${i + 1}`;
          return v.length > 22 ? v.slice(0, 21) + "…" : v;
        },
      },
    },
    y: {
      beginAtZero: true,
      border: { display: false },
      grid: { color: theme["line-subtle"] },
      ticks: { color: theme.muted, font: { size: 11 } },
    },
  };
  options.plugins.tooltip.callbacks = {
    title: (items) =>
      dimension
        ? label(shown[items[0].dataIndex][dimension])
        : `Row ${items[0].dataIndex + 1}`,
    label: (item) => `${measureLabel}: ${format(item.raw)}${unit}`,
  };
  return replace(id, {
    type: "bar",
    data: {
      labels: shown.map((r, i) =>
        dimension ? label(r[dimension]) : `Row ${i + 1}`,
      ),
      datasets: [
        {
          data: shown.map((r) => Number(r[measure])),
          backgroundColor: theme["chart-bar"],
          borderRadius: 3,
          maxBarThickness: 40,
        },
      ],
    },
    options,
  });
}
export function destroy(id) {
  charts.get(id)?.destroy();
  charts.delete(id);
}
export function assistant(rows, label, groupBy, percent) {
  if (groupBy !== "year")
    return catalogue(
      rows,
      "group",
      "value",
      label,
      "ai-chart",
      percent ? "%" : "",
      percent ? "Share" : "Count",
    );
  const byYear = new Map(
    rows.map((r) => [
      Number(r.group.split("/").pop().slice(2, 6)),
      r.value == null ? null : Number(r.value),
    ]),
  );
  const years = [...byYear.keys()];
  const complete = Array.from(
    { length: Math.max(...years) - Math.min(...years) + 1 },
    (_, i) => Math.min(...years) + i,
  );
  const options = base();
  options.scales = {
    x: {
      grid: { display: false },
      ticks: { color: theme.muted },
      border: { display: false },
    },
    y: {
      beginAtZero: true,
      grid: { color: theme["line-subtle"] },
      ticks: {
        color: theme.muted,
        callback: (v) => format(v) + (percent ? "%" : ""),
      },
      border: { display: false },
    },
  };
  return replace("ai-chart", {
    type: "line",
    data: {
      labels: complete.map((y) => `${y}–${y + 1}`),
      datasets: [
        {
          data: complete.map((y) => byYear.get(y) ?? null),
          borderColor: theme.accent,
          backgroundColor: theme.accent,
          borderWidth: 2,
          pointRadius: 4,
          spanGaps: false,
          tension: 0.15,
        },
      ],
    },
    options,
  });
}
export function image(id) {
  return charts.get(id)?.toBase64Image();
}
export function resize(id) {
  charts.get(id)?.resize();
}

// Update existing charts (including AI results) without issuing new database queries.
window.addEventListener("unistats:themechange", () => {
  const old = { ...theme };
  const next = getComputedStyle(document.documentElement);
  for (const key of Object.keys(theme))
    theme[key] = next.getPropertyValue("--" + key).trim();
  const recolor = (value) => {
    if (Array.isArray(value)) return value.map(recolor);
    const key = Object.keys(old).find((key) => old[key] === value);
    return key ? theme[key] : value;
  };
  for (const chart of charts.values()) {
    const tooltip = chart.options.plugins.tooltip;
    Object.assign(tooltip, {
      backgroundColor: theme.raised,
      titleColor: theme.ink,
      bodyColor: theme.ink,
      borderColor: theme["line-subtle"],
    });
    for (const scale of Object.values(chart.options.scales || {})) {
      if (scale.ticks) scale.ticks.color = theme.muted;
      if (scale.grid) scale.grid.color = theme["line-subtle"];
    }
    for (const dataset of chart.data.datasets) {
      for (const key of [
        "backgroundColor",
        "borderColor",
        "pointBackgroundColor",
        "pointBorderColor",
        "hoverBackgroundColor",
      ]) {
        if (dataset[key]) dataset[key] = recolor(dataset[key]);
      }
    }
    chart.update("none");
  }
});
