"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import type { ChatMessage as Message } from "@/lib/types";
import ChatMessage from "./ChatMessage";

interface Props {
  messages: Message[];
  loading: boolean;
  onSend: (message: string) => void;
}

/** Collapsible AI assistant sidebar. */
export default function ChatPanel({ messages, loading, onSend }: Props) {
  const [open, setOpen] = useState(true);
  const [input, setInput] = useState("");
  const list = useRef<HTMLUListElement>(null);

  useEffect(() => {
    if (list.current) list.current.scrollTop = list.current.scrollHeight;
  }, [messages, loading]);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!input.trim() || loading) return;
    onSend(input.trim());
    setInput("");
  };

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="w-8 shrink-0 border-l border-rule bg-panel text-[13px] font-semibold text-amber [writing-mode:vertical-rl]"
      >
        Open FinAlly assistant
      </button>
    );
  }

  return (
    <aside className="flex w-[340px] shrink-0 flex-col border-l border-rule bg-panel">
      <header className="flex h-7 items-center justify-between border-b border-rule px-2">
        <h2 className="text-[13px] font-semibold text-amber">FinAlly assistant</h2>
        <button onClick={() => setOpen(false)} aria-label="Collapse chat" className="text-muted hover:text-ink">
          »
        </button>
      </header>
      <ul ref={list} className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-2">
        {messages.length === 0 && (
          <li className="text-sm text-muted">
            Ask about your portfolio, or tell me what to trade. Try &quot;Buy 5 NVDA&quot; or &quot;How concentrated am I?&quot;
          </li>
        )}
        {messages.map((m, i) => (
          <ChatMessage key={i} message={m} />
        ))}
        {loading && (
          <li data-testid="chat-loading" className="flex gap-1 self-start py-2" aria-label="Thinking">
            <span className="dot" />
            <span className="dot [animation-delay:150ms]" />
            <span className="dot [animation-delay:300ms]" />
          </li>
        )}
      </ul>
      <form onSubmit={submit} className="flex gap-1 border-t border-rule p-2">
        <input
          data-testid="chat-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Message FinAlly"
          className="field min-w-0 flex-1"
        />
        <button data-testid="chat-send" type="submit" disabled={loading} className="btn-submit">
          Send
        </button>
      </form>
    </aside>
  );
}
