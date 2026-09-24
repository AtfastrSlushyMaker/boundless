"use client";

import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { Check, Feather, ScrollText, Search, StopCircle } from "lucide-react";
import { useEffect, useState } from "react";

export type GenerationStage = "reading" | "writing" | "interpreting";

const STEPS: Array<{ id: GenerationStage; label: string; Icon: typeof Search }> = [
  { id: "reading", label: "Reading the world", Icon: Search },
  { id: "writing", label: "Writing the scene", Icon: Feather },
  { id: "interpreting", label: "Recording what changed", Icon: ScrollText },
];

function useElapsed(active: boolean) {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    if (!active) return;
    const started = Date.now();
    const reset = window.setTimeout(() => setSeconds(0), 0);
    const timer = window.setInterval(() => setSeconds(Math.floor((Date.now() - started) / 1000)), 500);
    return () => { window.clearTimeout(reset); window.clearInterval(timer); };
  }, [active]);
  return seconds;
}

/** What the engine is doing right now, with honest elapsed time and a way out. */
export function GenerationProgress({ stage, hasText, onStop }: { stage: GenerationStage; hasText: boolean; onStop: () => void }) {
  const reduceMotion = useReducedMotion();
  const elapsed = useElapsed(true);
  const current = STEPS.findIndex((step) => step.id === stage);
  const clock = `${Math.floor(elapsed / 60)}:${String(elapsed % 60).padStart(2, "0")}`;
  return <motion.section className={`generation-progress${hasText ? " is-compact" : ""}`} aria-live="polite"
    initial={{ opacity: 0, y: reduceMotion ? 0 : 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: reduceMotion ? 0 : -4 }}
    transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }}>
    <ol className="generation-steps">
      {STEPS.map((step, index) => {
        const state = index < current ? "done" : index === current ? "active" : "waiting";
        return <li key={step.id} data-state={state}>
          <span className="generation-step-icon" aria-hidden="true">
            {state === "done" ? <Check size={13} /> : <step.Icon size={13} />}
            {state === "active" && !reduceMotion && <motion.span className="generation-step-ring" layoutId="generation-ring" />}
          </span>
          <span className="generation-step-label">{step.label}</span>
          {index < STEPS.length - 1 && <span className="generation-step-line" data-filled={index < current} aria-hidden="true" />}
        </li>;
      })}
    </ol>
    <div className="generation-meta"><span className="generation-clock" aria-label={`Elapsed ${clock}`}>{clock}</span>
      <button type="button" className="generation-stop" onClick={onStop}><StopCircle size={14} />Stop</button></div>
    <AnimatePresence>{!hasText && <motion.div key="skeleton" className="generation-skeleton" aria-hidden="true"
      initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0, height: 0 }}>
      {[92, 100, 84, 96, 58].map((width, index) => <span key={index} style={{ width: `${width}%`, animationDelay: `${index * 0.12}s` }} />)}
    </motion.div>}</AnimatePresence>
  </motion.section>;
}
