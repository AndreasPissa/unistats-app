import { test, expect } from "@playwright/test";

async function ready(page) {
  await expect(page.locator("#explorer-view")).toHaveAttribute(
    "aria-busy",
    "false",
  );
  await expect(page.locator("#explorer-results")).toBeVisible();
  await expect(page.locator("#error-banner")).toBeHidden();
}
async function change(page, selector, value) {
  const response = page.waitForResponse((r) =>
    r.url().includes("/api/explore?"),
  );
  await page.locator(selector).selectOption(value);
  await response;
  await ready(page);
}
test("desktop: real totals, linked filters, time, metrics, tables, CSV and query inspection", async ({
  page,
}) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/?source=snapshot");
  await ready(page);
  await expect(page.locator("#stat-total")).toHaveText("58,466");
  await expect(page.locator("#source-pill")).toHaveText("Local RDF snapshot");
  expect(
    await page.locator("#year-filter option").allTextContents(),
  ).not.toContain("2017–2018");
  await change(page, "#gender-filter", "Female");
  await expect(page.locator("#stat-total")).toHaveText("10,991");
  await expect(page.locator("#stat-female")).toHaveText("100%");
  await change(page, "#metric-filter", "femaleShare");
  await page.locator('[data-display="table"]').click();
  await expect(page.locator("#results-table tbody tr")).toHaveCount(12);
  const download = page.waitForEvent("download");
  await page.locator("#export-csv").click();
  expect((await download).suggestedFilename()).toMatch(
    /students-AY2023_2024-snapshot.csv/,
  );
  await page.locator("#view-query").click();
  await expect(page.locator("#explorer-code")).toContainText(
    "SELECT ?academicYear",
  );
  await expect(page.locator("#filter-code")).toContainText("Female");
  await page.getByRole("button", { name: "Close query", exact: true }).click();
  await page.locator("#reset-button").click();
  await ready(page);
  await change(page, "#year-filter", "AY2016_2017");
  await expect(page.locator("#timeline-year")).toHaveText("2016–2017");
  await page.locator("#year-slider").fill("6");
  await page.locator("#year-slider").dispatchEvent("change");
  await ready(page);
  await expect(page.locator("#stat-total")).toHaveText("58,466");
  await change(page, "#group-filter", "city");
  await page.locator("#results-table tbody .row-link").first().click();
  await ready(page);
  await expect(
    page.getByRole("button", { name: "Remove city filter" }),
  ).toBeVisible();
  await expect(page.locator("#results-table tbody tr")).toHaveCount(1);
  await page.screenshot({
    path: ".runtime/desktop-filtered.png",
    fullPage: true,
  });
  expect(errors).toEqual([]);
});

test("all explorers, postgraduate scope, dependent departments and clear empty state", async ({
  page,
}) => {
  await page.goto("/?source=snapshot");
  await ready(page);
  for (const view of ["postgraduates", "graduates", "staff", "entry"]) {
    await page.locator(`[data-view="${view}"]`).click();
    await ready(page);
    await expect(page.locator("#stat-total")).not.toHaveText("—");
    if (view === "postgraduates") {
      await change(page, "#level-filter", "MSc");
      await expect(page.locator("#distribution-legend")).not.toContainText(
        "PhD",
      );
    }
    if (view === "staff") {
      expect(
        await page.locator("#year-filter option").allTextContents(),
      ).not.toContain("2020–2021");
      await change(page, "#staff-filter", "external");
    }
  }
  await page.locator('[data-view="students"]').click();
  await ready(page);
  const university = await page
    .locator("#university-filter option")
    .nth(1)
    .getAttribute("value");
  await change(page, "#university-filter", university);
  expect(await page.locator("#department-filter option").count()).toBeLessThan(
    33,
  );
  const regionValues = await page
    .locator("#region-filter option")
    .evaluateAll((els) => els.slice(1).map((e) => e.value));
  for (const region of regionValues) {
    await change(page, "#region-filter", region);
    if (await page.locator("#empty-state").isVisible()) break;
  }
  await expect(page.locator("#empty-state")).toBeVisible();
  await expect(page.locator("#stat-total")).toHaveText("—");
});

test("catalogue: corrected query, filters, chart, export and AI navigation", async ({
  page,
}) => {
  await page.goto("/?source=snapshot&view=catalogue");
  await expect(page.locator(".query-option")).toHaveCount(41);
  await page.locator("#query-search").fill("05_master");
  await page.locator(".query-option").click();
  await page.locator('[name="programType"]').selectOption("MSc");
  await page.locator("#run-query").click();
  await expect(page.locator("#query-results")).toBeVisible();
  await expect(page.locator("#catalogue-code")).toContainText(
    "VALUES ?programType { res:MSc }",
  );
  await expect(page.locator("#query-table tbody tr")).toHaveCount(12);
  const download = page.waitForEvent("download");
  await page.locator("#query-export").click();
  expect((await download).suggestedFilename()).toContain(
    "05_master_filterable",
  );
  await page.locator('[name="gender"]').selectOption("Female");
  await expect(page.locator("#query-results")).toBeHidden();
  await page.locator("#query-search").fill("X05");
  await page.locator(".query-option").click();
  await page.locator("#run-query").click();
  await expect(page.locator("#query-results")).toBeVisible();
  await expect(page.locator("#error-banner")).toBeHidden();
  await page.screenshot({ path: ".runtime/catalogue.png", fullPage: true });
  await page.locator('[data-view="ai"]').click();
  await expect(page.locator("#ai-question")).toBeEditable();
  await expect(page.locator("#ai-form")).toBeVisible();
});

test("failed live requests never leave snapshot results labelled as live", async ({
  page,
}) => {
  await page.goto("/?source=snapshot");
  await ready(page);
  await page.route("**/api/explore?**", async (route) => {
    if (new URL(route.request().url()).searchParams.get("source") === "live")
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({
          detail:
            "The live database is unavailable. Try Auto or Local snapshot.",
        }),
      });
    else await route.continue();
  });
  await page.locator("#source-select").selectOption("live");
  await expect(page.locator("#error-banner")).toBeVisible();
  await expect(page.locator("#explorer-results")).toBeHidden();
  await expect(page.locator("#source-pill")).toHaveText("Request failed");
  await change(page, "#source-select", "snapshot");
  await expect(page.locator("#stat-total")).toHaveText("58,466");
});

test("phone: charts, navigation and catalogue fit the viewport", async ({
  page,
}) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/?source=snapshot");
  await ready(page);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  const chart = await page.locator("#ranking-chart").boundingBox();
  expect(chart.width).toBeLessThan(390);
  await page.screenshot({ path: ".runtime/mobile.png", fullPage: true });
  await page.locator('[data-display="table"]').click();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.locator('[data-view="catalogue"]').click();
  await page.locator("#run-query").click();
  await expect(page.locator("#query-results")).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: ".runtime/mobile-catalogue.png",
    fullPage: true,
  });
  expect(errors).toEqual([]);
});

test("optional WebMCP tools expose current state and use the same year control", async ({
  page,
}) => {
  await page.addInitScript(() => {
    window.registeredTools = {};
    Object.defineProperty(document, "modelContext", {
      value: {
        registerTool(tool) {
          window.registeredTools[tool.name] = tool;
        },
      },
    });
  });
  await page.goto("/?source=snapshot");
  await ready(page);
  await page.waitForFunction(
    () => Object.keys(window.registeredTools).length === 2,
  );
  const data = await page.evaluate(() =>
    window.registeredTools.read_unistats_explorer.execute({}),
  );
  expect(data.summary.total).toBe(58466);
  const changed = await page.evaluate(() =>
    window.registeredTools.set_unistats_academic_year.execute({
      year: "AY2016_2017",
    }),
  );
  expect(changed.year).toBe("AY2016_2017");
  await expect(page.locator("#year-filter")).toHaveValue("AY2016_2017");
});

test("clicking bars and donut segments filters the actual observations", async ({
  page,
}) => {
  await page.goto("/?source=snapshot");
  await ready(page);
  async function clickFirstPoint(id) {
    const position = await page.evaluate((chartId) => {
      const point = window.Chart.getChart(chartId).getDatasetMeta(0).data[0];
      if (chartId === "distribution-chart") {
        const angle = (point.startAngle + point.endAngle) / 2;
        const radius = (point.innerRadius + point.outerRadius) / 2;
        return {
          x: point.x + Math.cos(angle) * radius,
          y: point.y + Math.sin(angle) * radius,
        };
      }
      return { x: (point.x + point.base) / 2, y: point.y };
    }, id);
    const response = page.waitForResponse((r) =>
      r.url().includes("/api/explore?"),
    );
    await page.locator("#" + id).click({ position });
    await response;
    await ready(page);
  }
  await clickFirstPoint("ranking-chart");
  await expect(page.locator("#stat-departments")).toHaveText("1");
  await expect(
    page.getByRole("button", { name: "Remove department filter" }),
  ).toBeVisible();
  await page.locator("#reset-button").click();
  await ready(page);
  await clickFirstPoint("distribution-chart");
  await expect(page.locator("#category-filter")).toHaveValue(
    "BeyondNormalYears",
  );
  await expect(page.locator("#stat-total")).toHaveText("28,784");
  await expect(page.locator("#stat-third")).toHaveText("100%");
});

test("institution names open websites while separate controls retain filtering", async ({
  page,
  context,
}) => {
  await page.goto("/?source=snapshot");
  await ready(page);
  await expect(
    page.locator("footer, .topbar, #page-eyebrow, #page-description"),
  ).toHaveCount(0);
  await page.locator('[data-display="table"]').click();
  const firstRow = page.locator("#results-table tbody tr").first();
  const departmentLink = firstRow.locator(".institution-identity a");
  const destination = await departmentLink.getAttribute("href");
  expect(destination).toMatch(/^https?:\/\//);
  await expect(departmentLink).toHaveAttribute("target", "_blank");
  await expect(departmentLink).toHaveAttribute("rel", "noopener noreferrer");
  await expect(firstRow.locator(".cell-subtitle a")).toHaveAttribute(
    "href",
    "https://www.uniwa.gr/",
  );
  await context.route(destination, (route) =>
    route.fulfill({
      contentType: "text/html",
      body: "<title>Institution destination</title>",
    }),
  );
  const opened = page.waitForEvent("popup");
  await departmentLink.click();
  const popup = await opened;
  await expect(popup).toHaveURL(destination);
  await popup.close();
  await expect(page.locator("#stat-departments")).toHaveText("32");
  await firstRow.locator(".row-filter").click();
  await ready(page);
  await expect(page.locator("#stat-departments")).toHaveText("1");
  await expect(page.locator("#active-filters a")).toHaveAttribute(
    "href",
    destination,
  );
  await page.locator('[data-view="catalogue"]').click();
  await page.locator("#query-search").fill("01_school_metadata");
  await page.locator(".query-option").click();
  await page.locator("#run-query").click();
  await expect(page.locator("#query-results")).toBeVisible();
  const links = await page
    .locator("#query-table tbody a")
    .evaluateAll((els) =>
      els.map((a) => ({ href: a.href, text: a.textContent })),
    );
  expect(
    links.some(
      (a) =>
        a.text === "Technical University of Crete" &&
        a.href === "https://www.tuc.gr/el/archi",
    ),
  ).toBe(true);
  expect(
    links.some(
      (a) =>
        a.text === "School of Engineering" &&
        a.href === "https://www.tuc.gr/el/scholes",
    ),
  ).toBe(true);
  const program = links.find((a) => a.text.includes("BSc"));
  expect(program).toBeTruthy();
  expect(program.href).not.toContain("#BSc");
  const unsafe = await page.evaluate(async () => {
    const { institutionText } = await import("/assets/js/institutions.js");
    return institutionText(
      {
        institutions: { bad: { url: "javascript:alert(1)", label: "Bad" } },
        departments: [],
      },
      "bad",
      "Bad",
    ).tagName;
  });
  expect(unsafe).toBe("SPAN");
});
