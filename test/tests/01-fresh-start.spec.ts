import { expect, test } from "@playwright/test";
import { DEFAULT_TICKERS, openApp, readNumber, waitForPrice } from "./helpers";

test("fresh start shows default watchlist, $10k and streaming prices", async ({ page }) => {
  await openApp(page);

  for (const ticker of DEFAULT_TICKERS) {
    await expect(page.getByTestId(`watchlist-row-${ticker}`)).toBeVisible();
  }
  await expect(page.locator('[data-testid^="watchlist-row-"]')).toHaveCount(DEFAULT_TICKERS.length);

  await expect.poll(() => readNumber(page.getByTestId("header-cash"))).toBe(10000);
  await expect.poll(() => readNumber(page.getByTestId("header-total-value"))).toBe(10000);

  await waitForPrice(page, "AAPL");
  const first = await page.getByTestId("price-AAPL").textContent();
  await expect.poll(() => page.getByTestId("price-AAPL").textContent()).not.toBe(first);
});

test("clicking a ticker shows the main chart", async ({ page }) => {
  await openApp(page);
  await page.getByTestId("watchlist-row-MSFT").click();
  await expect(page.getByTestId("main-chart")).toBeVisible();
  await expect(page.getByTestId("main-chart")).toContainText("MSFT");
});
