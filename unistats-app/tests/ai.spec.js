import { test, expect } from "@playwright/test";

const answer = {
  status: "answer",
  kind: "statistics",
  answer: "2 results from the live database.",
  interpretation: "Postgraduate students by level: 2023–2024.",
  assumptions: [],
  note: "Includes MSc and PhD students only.",
  unit: "students",
  plan: { group_by: "level", metric: "total" },
  columns: ["group", "value"],
  rows: [
    { group: "http://www.ics.forth.gr/uniStats/resource/MSc", value: "9000" },
    { group: "http://www.ics.forth.gr/uniStats/resource/PhD", value: "2593" },
  ],
  query: "SELECT ?group ?value WHERE { ?s ?p ?o }",
  executed: true,
  totalRows: 2,
  source: { mode: "live", cached: false, retrievedAt: "2026-10-10T12:00:00Z" },
  timing: { totalMs: 1000 },
  requestId: "test1234",
  model: "test-model",
};

test.beforeEach(async ({ page }) => {
  await page.route("**/api/ai/status", (route) =>
    route.fulfill({
      json: {
        enabled: true,
        ready: true,
        model: "test-model",
        message: "Ready · hosted API · live database",
      },
    }),
  );
});

test("question submits live, renders a chart and table, exports, and shows its query", async ({
  page,
}) => {
  let request;
  await page.route("**/api/ai/query", async (route) => {
    request = route.request().postDataJSON();
    await route.fulfill({ json: answer });
  });
  await page.goto("/?view=ai&source=snapshot");
  await expect(page.locator(".connection")).toBeHidden();
  await page.getByRole("button", { name: "MSc vs PhD" }).click();
  await page.locator("#ai-submit").click();
  await expect(page.locator("#ai-result")).toBeVisible();
  expect(request).toEqual({
    question: "Compare MSc and PhD student counts in the latest academic year.",
  });
  await expect(page.locator("#ai-table tbody tr")).toHaveCount(2);
  await expect(page.locator("#ai-chart")).toBeVisible();
  await page.locator(".ai-query-details summary").click();
  await expect(page.locator("#ai-query")).toContainText("SELECT");
  const download = page.waitForEvent("download");
  await page.locator("#ai-export").click();
  expect((await download).suggestedFilename()).toBe("unistats-ai-test1234.csv");
  await page.locator("#ai-question").fill("And just the women?");
  await page.locator("#ai-followup").check();
  await page.locator("#ai-submit").click();
  await expect(page.locator("#ai-status")).toContainText("Complete");
  expect(request.previous_question).toContain("Compare MSc and PhD");
});

test("clarification and live failures hide any previous results", async ({
  page,
}) => {
  let next = answer,
    code = 200;
  await page.route("**/api/ai/query", (route) =>
    route.fulfill({ status: code, json: next }),
  );
  await page.goto("/?view=ai");
  await page.locator("#ai-question").fill("Compare MSc and PhD");
  await page.locator("#ai-submit").click();
  await expect(page.locator("#ai-result")).toBeVisible();
  next = {
    status: "clarification",
    message: "2017–2018 has no observations.",
    executed: false,
  };
  await page.locator("#ai-question").fill("What about 2017?");
  await page.locator("#ai-submit").click();
  await expect(page.locator("#ai-message")).toContainText("no observations");
  await expect(page.locator("#ai-result")).toBeHidden();
  next = {
    detail: "The live database is unavailable. No snapshot was substituted.",
  };
  code = 503;
  await page.locator("#ai-submit").click();
  await expect(page.locator("#ai-message")).toContainText("No snapshot");
  await expect(page.locator("#ai-result")).toBeHidden();
  await expect(page.locator("#ai-question")).toBeEditable();
});

test("setup state is clear and mobile page fits the viewport", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route("**/api/ai/status", (route) =>
    route.fulfill({
      json: {
        ready: false,
        message:
          "AI setup pending. Add provider details to the backend .env file.",
      },
    }),
  );
  await page.goto("/?view=ai");
  await expect(page.locator("#ai-status")).toContainText("AI setup pending");
  await expect(page.locator("#ai-submit")).toBeDisabled();
  await expect(page.locator("#ai-question")).toBeEditable();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

test("year charts preserve gaps and model text is rendered as text", async ({
  page,
}) => {
  const trend = {
    ...answer,
    plan: { group_by: "year", metric: "total" },
    rows: [
      {
        group: "http://www.ics.forth.gr/uniStats/resource/AY2016_2017",
        value: "42",
      },
      {
        group: "http://www.ics.forth.gr/uniStats/resource/AY2018_2019",
        value: "52",
      },
    ],
    interpretation: '<img src=x onerror="window.untrusted=true">',
  };
  await page.route("**/api/ai/query", (route) =>
    route.fulfill({ json: trend }),
  );
  await page.goto("/?view=ai");
  await page.locator("#ai-question").fill("Enrollment over time");
  await page.locator("#ai-submit").click();
  await expect(page.locator("#ai-chart")).toBeVisible();
  expect(
    await page.evaluate(
      () => window.Chart.getChart("ai-chart").data.datasets[0].data,
    ),
  ).toEqual([42, null, 52]);
  await expect(page.locator("#ai-interpretation img")).toHaveCount(0);
  expect(await page.evaluate(() => window.untrusted)).toBeUndefined();
});
