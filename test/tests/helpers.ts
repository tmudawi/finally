import { expect, type Locator, type Page } from "@playwright/test";

export const DEFAULT_TICKERS = ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "NVDA", "META", "JPM", "V", "NFLX"];

/** Parse a displayed money/number string such as "$10,000.00" into a number. */
export function parseMoney(text: string | null): number {
  return Number((text ?? "").replace(/[^0-9.-]/g, ""));
}

/** Read a locator's text as a number. */
export async function readNumber(locator: Locator): Promise<number> {
  return parseMoney(await locator.textContent());
}

/** Open the app and wait until the price stream is connected. */
export async function openApp(page: Page): Promise<void> {
  await page.goto("/");
  await expect(page.getByTestId("connection-status")).toHaveAttribute("data-status", "connected");
}

/** Wait until a ticker shows a numeric price in the watchlist. */
export async function waitForPrice(page: Page, ticker: string): Promise<void> {
  await expect(page.getByTestId(`price-${ticker}`)).toHaveText(/\d/);
}

/** Submit a market order through the trade bar. */
export async function trade(page: Page, ticker: string, quantity: number, side: "buy" | "sell"): Promise<void> {
  await page.getByTestId("trade-ticker").fill(ticker);
  await page.getByTestId("trade-quantity").fill(String(quantity));
  await page.getByTestId(`trade-${side}`).click();
}
