import { formatMoney } from "@/lib/portfolio";
import type { ConnectionStatus } from "@/lib/types";

const DOT: Record<ConnectionStatus, string> = {
  connected: "bg-up",
  reconnecting: "bg-amber",
  disconnected: "bg-down",
};

interface Props {
  totalValue: number;
  cash: number;
  status: ConnectionStatus;
}

/** Top strip: brand, live total value, cash and stream status. */
export default function Header({ totalValue, cash, status }: Props) {
  return (
    <header className="flex h-11 shrink-0 items-center gap-6 border-b border-rule bg-panel px-3">
      <div className="text-lg font-bold tracking-tight">
        <span className="text-amber">Fin</span>Ally
      </div>
      <dl className="flex items-baseline gap-5 text-sm">
        <div className="flex items-baseline gap-2">
          <dt className="text-muted">Portfolio</dt>
          <dd data-testid="header-total-value" className="text-base font-semibold tabular-nums">
            {formatMoney(totalValue)}
          </dd>
        </div>
        <div className="flex items-baseline gap-2">
          <dt className="text-muted">Cash</dt>
          <dd data-testid="header-cash" className="tabular-nums">
            {formatMoney(cash)}
          </dd>
        </div>
      </dl>
      <div className="ml-auto flex items-center gap-2 text-xs text-muted">
        <span
          data-testid="connection-status"
          data-status={status}
          title={status}
          className={`h-2.5 w-2.5 rounded-full ${DOT[status]}`}
        />
        <span className="capitalize">{status}</span>
      </div>
    </header>
  );
}
