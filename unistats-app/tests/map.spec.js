import { test, expect } from "@playwright/test";

// Tests exercise map interaction without depending on tile-provider uptime or
// repeatedly downloading third-party tiles. A real tile check is done separately.
async function mapReady(page, failTiles = false) {
  await page.route("https://vector.openstreetmap.org/**", (route) =>
    failTiles
      ? route.abort()
      : route.fulfill({
          contentType: "application/vnd.mapbox-vector-tile",
          body: Buffer.alloc(0),
        }),
  );
  await page.goto("/?view=map&source=snapshot");
  await expect(page.locator("#map-count")).toHaveText(
    "32 locations · 20 universities",
  );
  await expect(page.locator(".map-location")).toHaveCount(32);
}

test("map filters, marker popup and department statistics stay connected", async ({
  page,
}) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.setViewportSize({ width: 1440, height: 1000 });
  await mapReady(page);
  await expect(page.locator(".connection")).toBeHidden();
  await expect(page.locator(".leaflet-control-attribution")).toContainText(
    "OpenStreetMap",
  );
  await page.locator("#map-university").selectOption("https://www.uth.gr/");
  await expect(page.locator("#map-count")).toHaveText(
    "4 locations · 1 university",
  );
  await page
    .locator("#map-region")
    .selectOption("http://dbpedia.org/resource/Crete");
  await expect(page.locator("#map-directory-count")).toHaveText("0");
  await expect(page.locator("#map-fit")).toBeDisabled();
  await expect(page.locator(".map-no-results")).toBeVisible();
  await page.locator("#map-reset").click();
  await expect(page.locator(".map-location")).toHaveCount(32);
  await page.locator("#map-search").fill("Corfu");
  await expect(page.locator("#map-directory-count")).toHaveText("1");
  await page.locator(".campus-marker").click();
  await expect(page.locator(".campus-popup")).toContainText(
    "Ionian University",
  );
  await expect(page.locator(".campus-popup")).toContainText(
    "39.62051, 19.91227",
  );
  await expect(page.locator(".campus-popup h3 a")).toHaveAttribute(
    "href",
    "https://di.ionio.gr/",
  );
  await page.locator(".leaflet-popup-close-button").click();
  await expect(page.locator(".campus-place-name")).toBeHidden();
  await page.getByRole("button", { name: "Place names", exact: false }).click();
  await expect(page.locator("#map-labels")).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(page.locator(".campus-place-name")).toBeVisible();
  await expect(page.locator(".campus-place-name")).toHaveText("Corfu");
  await page.locator(".campus-marker").click();
  await page
    .getByRole("button", { name: "Department statistics", exact: true })
    .click();
  await expect(page.locator("#explorer-view")).toHaveAttribute(
    "aria-busy",
    "false",
  );
  await expect(page.locator("#department-filter")).toHaveValue(
    "https://di.ionio.gr/",
  );
  await expect(page.locator("#stat-departments")).toHaveText("1");
  await expect(page.locator("#error-banner")).toBeHidden();
  await page.locator('[data-view="map"]').click();
  await expect(page.locator("#map-directory-count")).toHaveText("1");
  expect(errors).toEqual([]);
});

test("nearby campuses remain selectable and university statistics include all its departments", async ({
  page,
}) => {
  await mapReady(page);
  await page.locator("#map-university").selectOption("https://unipi.gr/");
  await expect(page.locator(".map-location")).toHaveCount(2);
  // At street level even the two almost identical workbook coordinates must
  // expand into separate, clickable points.
  for (let i = 0; i < 4; i++)
    await page.getByRole("button", { name: "Zoom in", exact: true }).click();
  await page.locator(".campus-cluster").click();
  await expect(page.locator(".campus-marker")).toHaveCount(2);
  await page.locator(".campus-marker").first().click();
  await expect(page.locator(".campus-popup")).toBeVisible();
  await page.locator(".map-show-location").first().click();
  await expect(page.locator(".campus-popup")).toContainText("Digital Systems");
  await page.locator(".map-show-location").nth(1).click();
  await expect(page.locator(".campus-popup")).toContainText("Informatics");
  await page
    .getByRole("button", { name: "University statistics", exact: true })
    .click();
  await expect(page.locator("#explorer-view")).toHaveAttribute(
    "aria-busy",
    "false",
  );
  await expect(page.locator("#university-filter")).toHaveValue(
    "https://unipi.gr/",
  );
  await expect(page.locator("#department-filter")).toHaveValue("");
  await expect(page.locator("#stat-departments")).toHaveText("2");
});

test("mobile map remains usable if tiles fail and markers support keyboard selection", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mapReady(page, true);
  await expect(page.locator("#map-tile-notice")).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.locator("#map-search").fill("Corfu");
  await expect(page.locator("#map-directory-count")).toHaveText("1");
  await page.locator(".campus-marker").focus();
  await page.locator(".campus-marker").press("Enter");
  await expect(page.locator(".campus-popup")).toBeVisible();
  await expect(page.locator(".campus-popup .map-osm-link")).toHaveAttribute(
    "target",
    "_blank",
  );
  await expect
    .poll(() =>
      page.evaluate(() => {
        const map = document
          .querySelector("#campus-map")
          .getBoundingClientRect();
        const popup = document
          .querySelector(".leaflet-popup")
          .getBoundingClientRect();
        const tools = document
          .querySelector(".map-tools")
          .getBoundingClientRect();
        return (
          popup.top >= tools.bottom &&
          popup.bottom <= map.bottom &&
          popup.left >= map.left &&
          popup.right <= map.right
        );
      }),
    )
    .toBe(true);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: ".runtime/campus-map-mobile-offline.png",
    fullPage: true,
  });
});
