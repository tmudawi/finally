import { formatMoney, formatPct, type LivePosition } from "@/lib/portfolio";
import Panel from "./Panel";

const HEADINGS = ["Symbol", "Qty", "Avg cost", "Price", "Unrealized P&L", "Change"];

/** Holdings with live P&L. */
export default function PositionsTable({ positions }: { positions: LivePosition[] }) {
  return (
    <Panel title="Positions">
      <div className="h-full overflow-auto">
        <table data-testid="positions-table" className="w-full text-sm tabular-nums">
          <thead className="sticky top-0 bg-panel text-xs text-muted">
            <tr>
              {HEADINGS.map((h, i) => (
                <th key={h} className={`px-2 py-1 font-normal ${i ? "text-right" : "text-left"}`}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {positions.map((p) => {
              const tone = p.pnl >= 0 ? "text-up" : "text-down";
              return (
                <tr key={p.ticker} data-testid={`position-row-${p.ticker}`} className="border-t border-rule/60">
                  <td className="px-2 py-1 font-semibold">{p.ticker}</td>
                  <td className="px-2 text-right">{p.quantity}</td>
                  <td className="px-2 text-right">{p.avgCost.toFixed(2)}</td>
                  <td className="px-2 text-right">{p.price.toFixed(2)}</td>
                  <td className={`px-2 text-right ${tone}`}>{formatMoney(p.pnl)}</td>
                  <td className={`px-2 text-right ${tone}`}>{formatPct(p.pnlPct)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {positions.length === 0 && (
          <p className="p-3 text-sm text-muted">No positions yet. Use the trade bar to buy.</p>
        )}
      </div>
    </Panel>
  );
}
