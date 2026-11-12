import * as charts from "./charts.js";
import { institutionText } from "./institutions.js";

const $ = (id) => document.getElementById(id);
let deps,
  initialized = false,
  busy = false,
  ready = false,
  result,
  page = 0,
  previousQuestion = "";
const pageSize = 12;

async function checkStatus() {
  if (busy) return;
  $("ai-refresh").disabled = true;
  try {
    const status = await deps.api("/api/ai/status");
    ready = status.ready;
    $("ai-status").textContent = status.message;
    $("ai-status").title = status.model;
  } catch (error) {
    ready = false;
    $("ai-status").textContent = error.message;
  } finally {
    $("ai-submit").disabled = !ready;
    $("ai-refresh").disabled = false;
  }
}

function table() {
  const head = document.createElement("tr");
  for (const column of result.columns) {
    const th = document.createElement("th");
    th.scope = "col";
    th.textContent =
      column === "value"
        ? result.unit === "%"
          ? "Percentage"
          : result.unit
        : deps.label(column === "group" ? result.plan.group_by : column);
    th.textContent =
      th.textContent.charAt(0).toUpperCase() + th.textContent.slice(1);
    head.append(th);
  }
  $("ai-table").tHead.replaceChildren(head);
  const body = $("ai-table").tBodies[0];
  body.replaceChildren();
  for (const row of result.rows.slice(page * pageSize, (page + 1) * pageSize)) {
    const tr = document.createElement("tr");
    for (const column of result.columns) {
      const td = document.createElement("td");
      const value = row[column];
      const numeric =
        value != null && value !== "" && Number.isFinite(Number(value));
      const text = numeric
        ? Intl.NumberFormat("en", { maximumFractionDigits: 2 }).format(
            Number(value),
          )
        : deps.label(value);
      td.append(institutionText(deps.metadata, value, text));
      tr.append(td);
    }
    body.append(tr);
  }
  const pages = Math.max(1, Math.ceil(result.rows.length / pageSize));
  $("ai-page").textContent =
    `${result.rows.length.toLocaleString()} rows · Page ${page + 1} of ${pages}`;
  $("ai-prev").disabled = page === 0;
  $("ai-next").disabled = page + 1 >= pages;
}

function render() {
  $("ai-result").hidden = false;
  $("ai-answer").textContent = result.answer;
  $("ai-interpretation").textContent = result.interpretation;
  $("ai-note").textContent = result.note;
  $("ai-query").textContent = result.query;
  $("ai-provenance").textContent =
    `${new Date(result.source.retrievedAt).toLocaleString()} · ${(result.timing.totalMs / 1000).toFixed(1)}s`;
  $("ai-assumptions").replaceChildren(
    ...result.assumptions.map((text) => {
      const li = document.createElement("li");
      li.textContent = text;
      return li;
    }),
  );
  $("ai-export").disabled = result.rows.length === 0;
  page = 0;
  table();
  charts.destroy("ai-chart");
  let showChart =
    result.kind === "statistics" &&
    result.plan.group_by !== "total" &&
    result.rows.some((r) => r.value != null);
  $("ai-chart-wrap").hidden = !showChart;
  if (showChart) {
    charts.assistant(
      result.rows,
      deps.label,
      result.plan.group_by,
      result.unit === "%",
    );
    $("ai-chart-hint").textContent =
      result.plan.group_by === "year"
        ? "Gaps indicate missing academic years. Hover for exact values."
        : `Showing ${Math.min(20, result.rows.length)} of ${result.rows.length} results. Hover for full labels and values.`;
  }
}

async function ask(event) {
  event.preventDefault();
  if (busy || !ready || !$("ai-form").reportValidity()) return;
  const question = $("ai-question").value.trim();
  if (question.length < 3) return;
  busy = true;
  $("ai-submit").disabled = true;
  $("ai-question").readOnly = true;
  $("ai-followup").disabled = true;
  $("ai-refresh").disabled = true;
  $("ai-result").hidden = true;
  $("ai-message").hidden = true;
  $("ai-form").setAttribute("aria-busy", "true");
  const start = Date.now();
  const progress = () => {
    $("ai-status").textContent =
      `Interpreting your question and querying live data… ${Math.floor((Date.now() - start) / 1000)}s`;
  };
  progress();
  const timer = setInterval(progress, 1000);
  try {
    const payload = { question };
    if ($("ai-followup").checked && previousQuestion)
      payload.previous_question = previousQuestion;
    const response = await deps.api("/api/ai/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (response.status === "clarification") {
      $("ai-message").textContent = response.message;
      $("ai-message").hidden = false;
    } else {
      result = response;
      render();
      // Carry the resolved interpretation, so successive follow-ups preserve scope.
      previousQuestion =
        `${question}\nInterpreted as: ${response.interpretation}`.slice(
          0,
          2000,
        );
      $("ai-followup-control").hidden = false;
    }
    $("ai-status").textContent = response.executed
      ? "Complete · fresh live query"
      : "Clarification needed · no query executed";
  } catch (error) {
    $("ai-message").textContent = error.message;
    $("ai-message").hidden = false;
    $("ai-status").textContent = "No answer returned";
  } finally {
    clearInterval(timer);
    busy = false;
    $("ai-question").readOnly = false;
    $("ai-followup").disabled = false;
    $("ai-followup").checked = false;
    $("ai-refresh").disabled = false;
    $("ai-submit").disabled = !ready;
    $("ai-form").removeAttribute("aria-busy");
  }
}

function exportCSV() {
  if (!result) return;
  const cell = (value) => {
    let text = String(value ?? "");
    if (/^[=+@\-\t\r]/.test(text)) text = "'" + text;
    return '"' + text.replaceAll('"', '""') + '"';
  };
  const csv = [
    result.columns,
    ...result.rows.map((row) => result.columns.map((c) => row[c])),
  ]
    .map((row) => row.map(cell).join(","))
    .join("\r\n");
  const url = URL.createObjectURL(
    new Blob(["\ufeff", csv], { type: "text/csv;charset=utf-8" }),
  );
  const a = document.createElement("a");
  a.href = url;
  a.download = `unistats-ai-${result.requestId.slice(0, 8)}.csv`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function showAssistant(dependencies) {
  deps = dependencies;
  if (!initialized) {
    initialized = true;
    $("ai-form").addEventListener("submit", ask);
    $("ai-refresh").addEventListener("click", checkStatus);
    $("ai-question").addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        $("ai-form").requestSubmit();
      }
    });
    document.querySelectorAll("[data-ai-example]").forEach((button) =>
      button.addEventListener("click", () => {
        if (busy) return;
        $("ai-question").value = button.dataset.aiExample;
        $("ai-followup").checked = false;
        $("ai-question").focus();
      }),
    );
    $("ai-prev").addEventListener("click", () => {
      page--;
      table();
    });
    $("ai-next").addEventListener("click", () => {
      page++;
      table();
    });
    $("ai-export").addEventListener("click", exportCSV);
    $("ai-copy").addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(result.query);
        deps.toast("SPARQL copied");
      } catch {
        deps.toast("Select the query text to copy it.");
      }
    });
  }
  checkStatus();
  if (result) charts.resize("ai-chart");
}
