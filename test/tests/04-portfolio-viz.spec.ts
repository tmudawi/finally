import { expect, test } from "@playwright/test";
import { openApp, trade, waitForPrice } from "./helpers";

test("heatmap and P&L chart render after a buy", async ({ page, request }) => {
  await openApp(page);
  await waitForPrice(page, "MSFT");
  await trade(page, "MSFT", 3, "buy");
  await expect(page.getByTestId("position-row-MSFT")).toBeVisible();

  const cell = page.getByTestId("heatmap-cell-MSFT");
  await expect(page.getByTestId("heatmap")).toBeVisible();
  await expect(cell).toBeVisible();
  const color = await cell.evaluate((el) => getComputedStyle(el).backgroundColor);
  expect(color).not.toBe("rgba(0, 0, 0, 0)");

  const history = await (await request.get("/api/portfolio/history")).json();
  expect(history.length).toBeGreaterThan(0);
  await expect(page.getByTestId("pnl-chart")).toBeVisible();
  await expect(page.getByTestId("pnl-chart").locator("canvas").first()).toBeVisible();
});
