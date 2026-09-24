"use client";

import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { BookOpen, Feather, History } from "lucide-react";
import { useState } from "react";
import type { PlayerProfile, ProfileEntry } from "@/lib/api";

export type ProfileCategory = keyof Omit<PlayerProfile, "updated_through_turn">;

function sourceLabel(entry: ProfileEntry) {
  if (entry.source === "premise") return "Premise";
  if (entry.source === "record") return entry.turn_index ? `Turn ${entry.turn_index}` : "Record";
  return entry.turn_index ? `Turn ${entry.turn_index}` : "Story";
}

/** One category of the living profile: what holds now, and how it changed. */
export function ProfileList({ entries, empty }: { entries: ProfileEntry[]; empty: string }) {
  const reduceMotion = useReducedMotion();
  const [showPast, setShowPast] = useState(false);
  const active = entries.filter((entry) => entry.status === "active");
  const past = entries.filter((entry) => entry.status !== "active");
  if (!entries.length) return <p className="lore-copy">{empty}</p>;
  return <div className="profile-list">
    <ul>
      <AnimatePresence initial={false}>
        {active.map((entry, index) => <motion.li key={entry.id} layout={!reduceMotion}
          initial={reduceMotion ? false : { opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0 }}
          transition={{ duration: 0.22, delay: reduceMotion ? 0 : Math.min(index * 0.03, 0.3) }}
          className={`profile-entry profile-entry--${entry.source}`}>
          <span className="profile-source">{entry.source === "premise" ? <Feather size={11} /> : entry.source === "record" ? <BookOpen size={11} /> : <History size={11} />}{sourceLabel(entry)}</span>
          <p>{entry.text}</p>
        </motion.li>)}
      </AnimatePresence>
    </ul>
    {past.length > 0 && <>
      <button type="button" className="profile-past-toggle" onClick={() => setShowPast(!showPast)}>
        {showPast ? "Hide how this changed" : `How this changed (${past.length})`}</button>
      <AnimatePresence initial={false}>{showPast && <motion.ul key="past" className="profile-past"
        initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }}>
        {past.map((entry) => <li key={entry.id}><p>{entry.text}</p>
          <span>{sourceLabel(entry)}{entry.ended_turn_index ? ` → turn ${entry.ended_turn_index}` : ""}{entry.note ? ` · ${entry.note}` : ""}</span></li>)}
      </motion.ul>}</AnimatePresence>
    </>}
  </div>;
}
