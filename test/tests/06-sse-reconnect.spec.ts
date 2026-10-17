import { expect, test } from "@playwright/test";
import { waitForPrice } from "./helpers";

test("price stream reconnects after the connection is blocked", async ({ page }) => {
  await page.route("**/api/stream/prices", (route) => route.abort());
  await page.goto("/");
  const status = page.getByTestId("connection-status");
  await expect(status).toHaveAttribute("data-status", /reconnecting|disconnected/);

  await page.unroute("**/api/stream/prices");
  await expect(status).toHaveAttribute("data-status", "connected", { timeout: 30_000 });

  await waitForPrice(page, "AAPL");
  const first = await page.getByTestId("price-AAPL").textContent();
  await expect.poll(() => page.getByTestId("price-AAPL").textContent()).not.toBe(first);
});
