import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ChatMessage } from "@/lib/types";
import ChatPanel from "./ChatPanel";

const messages: ChatMessage[] = [
  { role: "user", content: "Buy 10 AAPL and watch PYPL" },
  {
    role: "assistant",
    content: "Bought 10 AAPL for you.",
    actions: [
      { type: "trade", ticker: "AAPL", side: "buy", quantity: 10, status: "ok", price: 190.12 },
      { type: "watchlist", ticker: "PYPL", action: "add", status: "error", error: "Unknown ticker" },
    ],
  },
];

describe("ChatPanel", () => {
  it("renders user and assistant messages with inline action results", () => {
    render(<ChatPanel messages={messages} loading={false} onSend={() => {}} />);
    expect(screen.getByTestId("chat-message-user")).toHaveTextContent("Buy 10 AAPL");
    expect(screen.getByTestId("chat-message-assistant")).toHaveTextContent("Bought 10 AAPL for you.");

    const actions = screen.getAllByTestId("chat-action");
    expect(actions[0]).toHaveTextContent("Bought 10 AAPL at 190.12");
    expect(actions[0]).toHaveAttribute("data-status", "ok");
    expect(actions[1]).toHaveTextContent("Add PYPL to watchlist failed: Unknown ticker");
    expect(actions[1]).toHaveAttribute("data-status", "error");
  });

  it("renders a failed chat call as an error message", () => {
    const failed: ChatMessage = { role: "assistant", content: "AI assistant unavailable: timeout", error: true };
    render(<ChatPanel messages={[failed]} loading={false} onSend={() => {}} />);
    const bubble = screen.getByText("AI assistant unavailable: timeout");
    expect(bubble).toHaveAttribute("data-error", "true");
    expect(bubble).toHaveClass("text-down");
  });

  it("shows the loading indicator and disables send while waiting", () => {
    render(<ChatPanel messages={[]} loading onSend={() => {}} />);
    expect(screen.getByTestId("chat-loading")).toBeInTheDocument();
    expect(screen.getByTestId("chat-send")).toBeDisabled();
  });

  it("hides the loading indicator when idle", () => {
    render(<ChatPanel messages={[]} loading={false} onSend={() => {}} />);
    expect(screen.queryByTestId("chat-loading")).toBeNull();
  });

  it("sends the trimmed message and clears the input", () => {
    const onSend = vi.fn();
    render(<ChatPanel messages={[]} loading={false} onSend={onSend} />);
    const input = screen.getByTestId("chat-input");
    fireEvent.change(input, { target: { value: "  hello  " } });
    fireEvent.click(screen.getByTestId("chat-send"));
    expect(onSend).toHaveBeenCalledWith("hello");
    expect(input).toHaveValue("");
  });
});
