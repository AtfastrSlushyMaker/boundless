"use client";

import { AnimatePresence, motion } from "motion/react";
import { Monitor, Moon, Sun } from "lucide-react";
import { SchemePreference, useScheme } from "@/lib/scheme";

const NEXT: Record<SchemePreference, SchemePreference> = { system: "light", light: "dark", dark: "system" };
const LABEL: Record<SchemePreference, string> = { system: "Match system", light: "Light", dark: "Dark" };

/** Cycles system → light → dark. World moods keep their colours in both schemes. */
export function SchemeToggle() {
  const { preference, setPreference } = useScheme();
  const Icon = preference === "light" ? Sun : preference === "dark" ? Moon : Monitor;
  return <button type="button" className="icon-button scheme-toggle" onClick={() => setPreference(NEXT[preference])}
    aria-label={`Appearance: ${LABEL[preference]}. Switch to ${LABEL[NEXT[preference]]}`} title={`Appearance: ${LABEL[preference]}`}>
    <AnimatePresence mode="wait" initial={false}>
      <motion.span key={preference} initial={{ rotate: -60, opacity: 0, scale: 0.6 }} animate={{ rotate: 0, opacity: 1, scale: 1 }}
        exit={{ rotate: 60, opacity: 0, scale: 0.6 }} transition={{ duration: 0.2 }} style={{ display: "grid" }}><Icon size={17} /></motion.span>
    </AnimatePresence>
  </button>;
}
