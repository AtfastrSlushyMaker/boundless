"use client";

import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { Check, Monitor, Moon, Sun } from "lucide-react";
import { KeyboardEvent, useEffect, useRef, useState } from "react";
import { SchemePreference, useScheme } from "@/lib/scheme";

const OPTIONS: Array<{ id: SchemePreference; label: string; Icon: typeof Sun }> = [
  { id: "system", label: "Match system", Icon: Monitor }, { id: "light", label: "Light", Icon: Sun }, { id: "dark", label: "Dark", Icon: Moon },
];

/** Appearance menu: system, light, or dark. World moods keep their colours in both schemes. */
export function SchemeToggle() {
  const { preference, setPreference } = useScheme();
  const reduceMotion = useReducedMotion();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const current = OPTIONS.find((option) => option.id === preference) ?? OPTIONS[0];

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: PointerEvent) => { if (!rootRef.current?.contains(event.target as Node)) setOpen(false); };
    const onKey = (event: globalThis.KeyboardEvent) => { if (event.key === "Escape") { event.stopPropagation(); setOpen(false); } };
    window.addEventListener("pointerdown", onPointer);
    window.addEventListener("keydown", onKey, true);
    const timer = window.setTimeout(() => itemRefs.current[OPTIONS.findIndex((option) => option.id === preference)]?.focus(), 0);
    return () => { window.removeEventListener("pointerdown", onPointer); window.removeEventListener("keydown", onKey, true); window.clearTimeout(timer); };
  }, [open, preference]);

  const onMenuKey = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    const index = itemRefs.current.findIndex((item) => item === document.activeElement);
    const next = (index + (event.key === "ArrowDown" ? 1 : -1) + OPTIONS.length) % OPTIONS.length;
    itemRefs.current[next]?.focus();
  };
  const choose = (value: SchemePreference) => { setPreference(value); setOpen(false); };

  return <div className="scheme-menu" ref={rootRef}>
    <button type="button" className="icon-button scheme-toggle" aria-haspopup="menu" aria-expanded={open}
      aria-label={`Appearance: ${current.label}`} title={`Appearance: ${current.label}`} onClick={() => setOpen(!open)}>
      <AnimatePresence mode="wait" initial={false}>
        <motion.span key={preference} initial={{ rotate: -60, opacity: 0, scale: 0.6 }} animate={{ rotate: 0, opacity: 1, scale: 1 }}
          exit={{ rotate: 60, opacity: 0, scale: 0.6 }} transition={{ duration: reduceMotion ? 0 : 0.2 }} style={{ display: "grid" }}>
          <current.Icon size={17} /></motion.span>
      </AnimatePresence>
    </button>
    <AnimatePresence>
      {open && <motion.div className="scheme-menu-list" role="menu" aria-label="Appearance" onKeyDown={onMenuKey}
        initial={{ opacity: 0, scale: reduceMotion ? 1 : 0.96, y: reduceMotion ? 0 : -4 }} animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: reduceMotion ? 1 : 0.97 }} transition={{ duration: reduceMotion ? 0 : 0.14 }}>
        <small>Appearance</small>
        {OPTIONS.map(({ id, label, Icon }, index) => <button type="button" key={id} role="menuitemradio" aria-checked={preference === id}
          ref={(element) => { itemRefs.current[index] = element; }} onClick={() => choose(id)}>
          <Icon size={15} /><span>{label}</span>{preference === id && <Check size={14} />}
        </button>)}
      </motion.div>}
    </AnimatePresence>
  </div>;
}
