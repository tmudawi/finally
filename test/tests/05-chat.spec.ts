import { expect, test } from "@playwright/test";
import { openApp, waitForPrice } from "./helpers";

test("mocked AI chat executes a trade inline", async ({ page }) => {
  await openApp(page);
  await waitForPrice(page, "AAPL");

  await page.getByTestId("chat-input").fill("Please buy some Apple");
  await page.getByTestId("chat-send").click();

  await expect(page.getByTestId("chat-message-user").last()).toContainText("Please buy some Apple");
  await expect(page.getByTestId("chat-message-assistant").last()).toContainText("Mock: buying 1 AAPL");
  await expect(page.getByTestId("chat-action").last()).toContainText("AAPL");
  await expect(page.getByTestId("chat-action").last()).toHaveAttribute("data-status", "ok");
  await expect(page.getByTestId("chat-loading")).toHaveCount(0);
  await expect(page.getByTestId("position-row-AAPL")).toBeVisible();
});

test("mocked AI chat adds a watchlist ticker", async ({ page }) => {
  await openApp(page);
  await page.getByTestId("chat-input").fill("Watch PayPal for me");
  await page.getByTestId("chat-send").click();

  await expect(page.getByTestId("chat-message-assistant").last()).toContainText("Mock: adding PYPL");
  await expect(page.getByTestId("chat-action").last()).toHaveAttribute("data-status", "ok");
  await expect(page.getByTestId("watchlist-row-PYPL")).toBeVisible();
});
