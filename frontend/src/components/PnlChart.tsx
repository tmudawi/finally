"use client";

import type { Point } from "@/lib/types";
import LineChart from "./LineChart";
import Panel from "./Panel";

/** Total portfolio value over time from portfolio snapshots. */
export default function PnlChart({ points }: { points: Point[] }) {
  return (
    <Panel title="Portfolio value">
      <LineChart points={points} color="#ecad0a" testId="pnl-chart" />
    </Panel>
  );
}
