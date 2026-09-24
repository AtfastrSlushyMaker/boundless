"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";
import { useState } from "react";

/** Client-side pages over a list; resets to the first page when `resetKey` changes. */
export function usePaged<T>(items: T[], size: number, resetKey = "") {
  const [state, setState] = useState({ page: 0, key: resetKey });
  const page = state.key === resetKey ? state.page : 0;
  const pages = Math.max(1, Math.ceil(items.length / size));
  const current = Math.min(page, pages - 1);
  return {
    items: items.slice(current * size, current * size + size),
    page: current, pages, total: items.length, size,
    setPage: (next: number) => setState({ page: Math.max(0, Math.min(pages - 1, next)), key: resetKey }),
  };
}

export function Pager({ page, pages, total, size, setPage, label }: {
  page: number; pages: number; total: number; size: number; setPage: (page: number) => void; label: string;
}) {
  if (pages <= 1) return null;
  const from = page * size + 1, to = Math.min(total, from + size - 1);
  return <nav className="pager" aria-label={`${label} pages`}>
    <button type="button" className="icon-button icon-button--sm" onClick={() => setPage(page - 1)} disabled={page === 0} aria-label="Previous page"><ChevronLeft size={15} /></button>
    <span aria-live="polite">{from}–{to} <em>of {total}</em></span>
    {pages <= 7 && <div className="pager-dots" aria-hidden="true">{Array.from({ length: pages }, (_, index) => <i key={index} className={index === page ? "is-on" : ""} />)}</div>}
    <button type="button" className="icon-button icon-button--sm" onClick={() => setPage(page + 1)} disabled={page >= pages - 1} aria-label="Next page"><ChevronRight size={15} /></button>
  </nav>;
}
