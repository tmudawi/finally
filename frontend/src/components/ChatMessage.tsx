import type { ChatAction, ChatMessage as Message } from "@/lib/types";

/** One-line description of an executed (or failed) action. */
export function describeAction(a: ChatAction) {
  const ok = a.status === "ok";
  if (a.type === "trade") {
    const verb = a.side === "buy" ? (ok ? "Bought" : "Buy") : ok ? "Sold" : "Sell";
    const base = `${verb} ${a.quantity} ${a.ticker}`;
    return ok ? `${base}${a.price ? ` at ${a.price.toFixed(2)}` : ""}` : `${base} failed: ${a.error}`;
  }
  const adding = a.action === "add";
  const verb = adding ? (ok ? "Added" : "Add") : ok ? "Removed" : "Remove";
  const base = `${verb} ${a.ticker} ${adding ? "to" : "from"} watchlist`;
  return ok ? base : `${base} failed: ${a.error}`;
}

/** A chat bubble with inline action results beneath assistant replies. */
export default function ChatMessage({ message }: { message: Message }) {
  const isUser = message.role === "user";
  return (
    <li
      data-testid={`chat-message-${message.role}`}
      className={`max-w-[90%] text-sm ${isUser ? "self-end" : "self-start"}`}
    >
      <p
        data-error={message.error || undefined}
        className={`whitespace-pre-wrap rounded px-2.5 py-1.5 ${
          isUser
            ? "bg-blue/20 text-ink"
            : message.error
              ? "border border-down/60 bg-down/10 text-down"
              : "border border-rule bg-canvas"
        }`}
      >
        {message.content}
      </p>
      {message.actions?.map((a, i) => (
        <p
          key={i}
          data-testid="chat-action"
          data-status={a.status}
          className={`mt-1 border-l-2 pl-2 text-xs ${
            a.status === "ok" ? "border-up text-up" : "border-down text-down"
          }`}
        >
          {describeAction(a)}
        </p>
      ))}
    </li>
  );
}
