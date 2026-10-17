import type { ReactNode } from "react";

interface Props {
  title: string;
  aside?: ReactNode;
  className?: string;
  children: ReactNode;
}

/** Bordered terminal panel with an amber title bar. */
export default function Panel({ title, aside, className = "", children }: Props) {
  return (
    <section className={`flex h-full min-h-0 min-w-0 flex-col border border-rule bg-panel ${className}`}>
      <header className="flex h-7 shrink-0 items-center justify-between border-b border-rule px-2">
        <h2 className="text-[13px] font-semibold text-amber">{title}</h2>
        {aside}
      </header>
      <div className="min-h-0 flex-1">{children}</div>
    </section>
  );
}
