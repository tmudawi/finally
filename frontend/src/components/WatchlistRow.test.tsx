import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FLASH_MS } from "@/hooks/useFlash";
import { mergePrices } from "@/hooks/usePriceStream";
import type { Direction, PriceTick } from "@/lib/types";
import WatchlistRow from "./WatchlistRow";

vi.mock("./LineChart", () => ({ default: () => null }));

const tick = (price: number, direction: Direction, timestamp = "2026-10-17T10:00:00Z"): PriceTick => ({
  price,
  prev_price: price,
  change_pct: 1.5,
  timestamp,
  direction,
});

const row = (t?: PriceTick) => (
  <WatchlistRow ticker="AAPL" tick={t} points={[]} selected={false} onSelect={() => {}} onRemove={() => {}} />
);

describe("WatchlistRow price flash", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("flashes green on an uptick and fades after FLASH_MS", () => {
    const { rerender } = render(row(tick(190, "flat")));
    const price = screen.getByTestId("price-AAPL");
    expect(price).not.toHaveClass("flash-up");

    rerender(row(tick(191, "up")));
    expect(price).toHaveTextContent("191.00");
    expect(price).toHaveClass("flash-up");

    act(() => vi.advanceTimersByTime(FLASH_MS));
    expect(price).not.toHaveClass("flash-up");
  });

  it("flashes red on a downtick", () => {
    const { rerender } = render(row());
    rerender(row(tick(189, "down")));
    expect(screen.getByTestId("price-AAPL")).toHaveClass("flash-down");
  });

  it("does not flash on a flat tick", () => {
    render(row(tick(190, "flat")));
    expect(screen.getByTestId("price-AAPL").className).not.toMatch(/flash-/);
  });
});

describe("mergePrices", () => {
  it("keeps the old object for an unchanged tick so it does not re-flash", () => {
    const old = tick(190, "up");
    const merged = mergePrices({ AAPL: old }, { AAPL: tick(190, "up"), MSFT: tick(400, "down") });
    expect(merged.AAPL).toBe(old);
    expect(merged.MSFT.price).toBe(400);
  });

  it("replaces a changed tick", () => {
    const merged = mergePrices({ AAPL: tick(190, "up") }, { AAPL: tick(191, "up", "2026-10-17T10:00:01Z") });
    expect(merged.AAPL.price).toBe(191);
  });
});
