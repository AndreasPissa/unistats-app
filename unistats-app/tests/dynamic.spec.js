import { test, expect } from "@playwright/test";

async function change(page, selector, value) {
  const response = page.waitForResponse(
    (r) => r.url().includes("/api/explore?") && r.status() === 200,
  );
  await page.locator(selector).selectOption(value);
  await response;
  await expect(page.locator("#explorer-view")).toHaveAttribute(
    "aria-busy",
    "false",
  );
}

test("staff hierarchy filters broad groups and individual ranks", async ({
  page,
}) => {
  await page.goto("/?view=staff&source=snapshot");
  await expect(page.locator("#stat-total")).toHaveText("1,342");
  await expect(page.locator("#distribution-title")).toHaveText(
    "By broad staff category",
  );
  await change(page, "#staff_group-filter", "RegularTeachingResearchStaff");
  await expect(page.locator("#category-filter option")).toHaveCount(5);
  const broaderTotal = await page.locator("#stat-total").textContent();
  await change(page, "#category-filter", "Professor");
  expect(await page.locator("#stat-total").textContent()).not.toBe(
    broaderTotal,
  );
  await change(page, "#category-detail-filter", "specific");
  await expect(page.locator("#distribution-legend")).toContainText(
    "Professors",
  );
  await expect(page.locator("#birth_year-control")).toBeHidden();
  await page.locator("#reset-button").click();
  await expect(page.locator("#stat-total")).toHaveText("1,342");
});

test("birth-year filters and separate graduate tables are explicit", async ({
  page,
}) => {
  await page.goto("/?view=postgraduates&source=snapshot");
  await expect(page.locator("#stat-total")).toHaveText("11,593");
  await change(page, "#birth_year-filter", "2000");
  await expect(page.locator("#active-filters")).toContainText("2000");
  await expect(page).toHaveURL(/birth_year=2000/);
  await page.locator('[data-view="graduates"]').click();
  await expect(page.locator("#stat-total")).toHaveText("3,510");
  await change(page, "#birth_year-filter", "2000");
  await expect(page.locator("#category-control")).toBeHidden();
  await expect(
    page.locator('#metric-filter option[value="excellentShare"]'),
  ).toHaveCount(0);
  await expect(page.locator("#dataset-note")).toContainText(
    "separate aggregate tables",
  );
  await expect(page.locator("#distribution-title")).toHaveText("By gender");
  const filtered = page.waitForResponse(
    (r) => r.url().includes("/api/explore?") && r.status() === 200,
  );
  await page
    .locator("#distribution-legend")
    .getByRole("button", { name: /^Female/ })
    .click();
  await filtered;
  await expect(page.locator("#gender-filter")).toHaveValue("Female");
  await expect(page.locator("#stat-female")).toHaveText("100%");
  await change(page, "#birth_year-filter", "");
  await expect(page.locator("#distribution-title")).toHaveText(
    "By degree grade",
  );
  await expect(page.locator("#category-control")).toBeVisible();
  await expect(
    page.locator('#metric-filter option[value="excellentShare"]'),
  ).toHaveCount(1);
});

test("light theme updates charts, survives reload and restores the original dark theme", async ({
  page,
}) => {
  await page.goto("/?source=snapshot");
  await expect(page.locator("#stat-total")).toHaveText("58,466");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  const color = () =>
    page.evaluate(() =>
      getComputedStyle(document.documentElement)
        .getPropertyValue("--canvas")
        .trim(),
    );
  expect(await color()).toBe("#101613");
  await page.getByRole("button", { name: "Switch to light mode" }).click();
  expect(await color()).toBe("#f3f6f4");
  expect(
    await page.evaluate(
      () => window.Chart.getChart("ranking-chart").options.scales.x.ticks.color,
    ),
  ).toBe("#52685b");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await expect(page.locator("#stat-total")).toHaveText("58,466");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.getByRole("button", { name: "Switch to dark mode" }).click();
  expect(await color()).toBe("#101613");
});

test("refresh discovers new year and institution choices without a page reload", async ({
  page,
}) => {
  await page.goto("/?source=snapshot");
  await expect(page.locator("#stat-total")).toHaveText("58,466");
  const baseline = await (
    await page.request.get("/api/explore?source=snapshot")
  ).json();
  const updated = structuredClone(baseline.metadata);
  updated.revision = "future-data-fixture";
  updated.availableYears.push("AY2030_2031");
  updated.departments.push({
    ...updated.departments[0],
    uri: "https://future.example/department",
    label: "Future Department",
    university: "https://future.example/university",
    universityLabel: "Future University",
  });
  await page.route("**/api/metadata?**", (route) =>
    route.fulfill({ json: updated }),
  );
  let fresh = false;
  await page.route("**/api/explore?**", (route) => {
    fresh =
      new URL(route.request().url()).searchParams.get("refresh") === "true";
    return route.fulfill({
      json: {
        ...baseline,
        metadata: updated,
        availableYears: updated.availableYears,
      },
    });
  });
  await page.locator("#refresh-data").click();
  await expect(page.locator("#refresh-data")).toBeEnabled();
  expect(fresh).toBe(true);
  await expect(
    page.locator(
      '#university-filter option[value="https://future.example/university"]',
    ),
  ).toHaveCount(1);
  await expect(
    page.locator('#year-filter option[value="AY2030_2031"]'),
  ).toHaveCount(1);
  await page.locator('[data-view="catalogue"]').click();
  await expect(
    page.locator(
      '#query-filters select[name="academicYear"] option[value="AY2030_2031"]',
    ),
  ).toHaveCount(1);
});
