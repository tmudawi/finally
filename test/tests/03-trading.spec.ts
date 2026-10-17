import { expect, test } from "@playwright/test";
import { openApp, readNumber, trade, waitForPrice } from "./helpers";

test("buy then sell shares", async ({ page }) => {
  await openApp(page);
  await waitForPrice(page, "AAPL");
  const startCash = await readNumber(page.getByTestId("header-cash"));

  await trade(page, "AAPL", 2, "buy");
  const row = page.getByTestId("position-row-AAPL");
  await expect(row).toBeVisible();
  await expect(page.getByTestId("positions-table")).toContainText("AAPL");
  await expect.poll(() => readNumber(page.getByTestId("header-cash"))).toBeLessThan(startCash);
  const afterBuy = await readNumber(page.getByTestId("header-cash"));

  await trade(page, "AAPL", 2, "sell");
  await expect(row).toHaveCount(0);
  await expect.poll(() => readNumber(page.getByTestId("header-cash"))).toBeGreaterThan(afterBuy);
});

test("selling more than held shows an error", async ({ page }) => {
  await openApp(page);
  await waitForPrice(page, "NFLX");
  await trade(page, "NFLX", 1000, "sell");
  await expect(page.getByTestId("trade-error")).toBeVisible();
  await expect(page.getByTestId("position-row-NFLX")).toHaveCount(0);
});
