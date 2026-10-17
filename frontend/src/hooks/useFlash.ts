"use client";

import { useEffect, useState } from "react";
import type { PriceTick } from "@/lib/types";

export const FLASH_MS = 500;

/** Return "flash-up" / "flash-down" for FLASH_MS after each up or down tick. */
export function useFlash(tick: PriceTick | undefined) {
  const [expired, setExpired] = useState<PriceTick | undefined>(undefined);

  useEffect(() => {
    const timer = setTimeout(() => setExpired(tick), FLASH_MS);
    return () => clearTimeout(timer);
  }, [tick]);

  if (!tick || tick === expired || tick.direction === "flat") return "";
  return `flash-${tick.direction}`;
}
