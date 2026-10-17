import { expect, test } from "@playwright/test";
import { openApp, waitForPrice } from "./helpers";

test("add and remove a watchlist ticker", async ({ page, request }) => {
  await openApp(page);

  await page.getByTestId("watchlist-add-input").fill("zzzz");
  await page.getByTestId("watchlist-add-button").click();
  await expect(page.getByTestId("watchlist-row-ZZZZ")).toBeVisible();
  await waitForPrice(page, "ZZZZ");

  await page.getByTestId("watchlist-row-ZZZZ").hover();
  await page.getByTestId("watchlist-remove-ZZZZ").click();
  await expect(page.getByTestId("watchlist-row-ZZZZ")).toHaveCount(0);

  const tickers = (await (await request.get("/api/watchlist")).json()).map((w: { ticker: string }) => w.ticker);
  expect(tickers).not.toContain("ZZZZ");
});
