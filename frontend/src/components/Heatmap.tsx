import { formatPct, type LivePosition } from "@/lib/portfolio";
import Panel from "./Panel";

/** Map P&L % to a green or red fill, saturating at +/-5%. */
export function pnlColor(pnlPct: number) {
  const strength = Math.min(Math.abs(pnlPct) / 5, 1);
  const alpha = (0.18 + strength * 0.62).toFixed(2);
  return pnlPct >= 0 ? `rgba(63, 185, 80, ${alpha})` : `rgba(248, 81, 73, ${alpha})`;
}

/** Flexbox treemap: each position sized by weight, coloured by P&L. */
export default function Heatmap({ positions }: { positions: LivePosition[] }) {
  const sorted = [...positions].sort((a, b) => b.weight - a.weight);
  return (
    <Panel title="Heatmap">
      <div data-testid="heatmap" className="flex h-full flex-wrap gap-px p-px">
        {sorted.length === 0 && <p className="m-auto text-sm text-muted">Buy a position to see it here.</p>}
        {sorted.map((p) => (
          <div
            key={p.ticker}
            data-testid={`heatmap-cell-${p.ticker}`}
            style={{
              flexGrow: p.weight,
              flexBasis: `${Math.max(p.weight * 100 - 1, 12)}%`,
              background: pnlColor(p.pnlPct),
            }}
            className="flex min-w-0 flex-col items-center justify-center overflow-hidden text-xs"
          >
            <span className="font-bold">{p.ticker}</span>
            <span className="tabular-nums">{formatPct(p.pnlPct)}</span>
          </div>
        ))}
      </div>
    </Panel>
  );
}
