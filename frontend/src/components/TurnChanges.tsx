"use client";

import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { Backpack, Clock3, Fingerprint, Flag, HeartPulse, MapPin, Sparkles, UserPlus, Users, X } from "lucide-react";
import { useEffect, useState } from "react";
import type { TurnChange } from "@/lib/api";

const ICONS: Record<string, typeof Sparkles> = {
  character: UserPlus, identity: Fingerprint, relationship: Users, objective: Flag, item: Backpack,
  location: MapPin, ability: Sparkles, status: HeartPulse, secret: Fingerprint, faction: Users, time: Clock3,
};

export function changeTone(change: TurnChange): string {
  if (change.type === "objective" && /completed/i.test(change.text)) return "success";
  if (change.type === "objective" && /failed/i.test(change.text)) return "danger";
  if (change.type === "relationship") return /↓/.test(change.text) ? "cool" : "warm";
  if (change.type === "identity" || change.type === "ability" || change.type === "secret") return "reveal";
  if (change.type === "status") return "danger";
  return "neutral";
}

/** The world's bookkeeping for one passage, shown quietly under the narration. */
export function TurnChanges({ changes, fresh = false }: { changes: TurnChange[]; fresh?: boolean }) {
  const reduceMotion = useReducedMotion();
  const [expanded, setExpanded] = useState(false);
  if (!changes.length) return null;
  const visible = expanded ? changes : changes.slice(0, 4);
  return <div className="turn-changes" aria-label="What changed in the world">
    <ul>
      {visible.map((change, index) => {
        const Icon = ICONS[change.type] ?? Sparkles;
        return <motion.li key={`${change.type}-${change.text}`} className={`turn-change turn-change--${changeTone(change)}`}
          initial={fresh && !reduceMotion ? { opacity: 0, y: 6, scale: 0.96 } : false}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          transition={{ duration: 0.28, delay: fresh && !reduceMotion ? 0.08 * index : 0, ease: [0.22, 1, 0.36, 1] }}>
          <Icon size={12} aria-hidden="true" /><span>{change.text}</span>
        </motion.li>;
      })}
      {changes.length > 4 && <li><button type="button" className="turn-change-more" onClick={() => setExpanded(!expanded)}>
        {expanded ? "Show less" : `+${changes.length - 4} more`}</button></li>}
    </ul>
  </div>;
}

type Toast = { id: number; changes: TurnChange[] };

/** A short-lived summary of important changes after each turn completes. */
export function ChangeToasts({ latest }: { latest: { key: string; changes: TurnChange[] } | null }) {
  const reduceMotion = useReducedMotion();
  const [toasts, setToasts] = useState<Toast[]>([]);
  useEffect(() => {
    if (!latest) return;
    const important = latest.changes.filter((change) => ["objective", "identity", "ability", "secret", "character"].includes(change.type));
    if (!important.length) return;
    const id = Date.now();
    const show = window.setTimeout(() => setToasts((current) => [...current.slice(-2), { id, changes: important.slice(0, 3) }]), 0);
    const hide = window.setTimeout(() => setToasts((current) => current.filter((toast) => toast.id !== id)), 6500);
    return () => { window.clearTimeout(show); window.clearTimeout(hide); };
  }, [latest]);
  return <div className="change-toasts" aria-live="polite">
    <AnimatePresence initial={false}>
      {toasts.map((toast) => <motion.div key={toast.id} className="change-toast" layout
        initial={{ opacity: 0, x: reduceMotion ? 0 : 28, scale: reduceMotion ? 1 : 0.97 }}
        animate={{ opacity: 1, x: 0, scale: 1 }} exit={{ opacity: 0, x: reduceMotion ? 0 : 20 }}
        transition={{ type: "spring", stiffness: 420, damping: 34 }}>
        <div className="change-toast-head"><Sparkles size={13} /><span>The world remembers</span>
          <button type="button" aria-label="Dismiss" onClick={() => setToasts((current) => current.filter((row) => row.id !== toast.id))}><X size={13} /></button></div>
        <ul>{toast.changes.map((change) => <li key={change.text} className={`turn-change--${changeTone(change)}`}>{change.text}</li>)}</ul>
        <motion.span className="change-toast-timer" initial={{ scaleX: 1 }} animate={{ scaleX: 0 }} transition={{ duration: 6.4, ease: "linear" }} />
      </motion.div>)}
    </AnimatePresence>
  </div>;
}
