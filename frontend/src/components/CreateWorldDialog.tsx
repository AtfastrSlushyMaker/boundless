"use client";

import { useReducedMotion, AnimatePresence, motion } from "motion/react";
import { ArrowUpRight, LoaderCircle, Sparkles, X } from "lucide-react";
import { useEffect, useState } from "react";
import { z } from "zod";
import type { GameMode } from "@/lib/api";

const promptSchema = z.string().trim().min(12, "Give the world a little more to begin with.").max(30_000, "Keep the opening brief under 30,000 characters.");
const directionSchema = z.string().trim().min(1, "Add a detail or direction for the enhancement.").max(2_000, "Keep the direction under 2,000 characters.");

type Props = {
  open: boolean;
  busy: boolean;
  error?: string;
  onClose: () => void;
  onCreate: (prompt: string, gameMode: GameMode) => void;
  onEnhance: (prompt: string, direction: string) => Promise<string>;
};

export function CreateWorldDialog({ open, busy, error, onClose, onCreate, onEnhance }: Props) {
  const reduceMotion = useReducedMotion();
  const [prompt, setPrompt] = useState("");
  const [direction, setDirection] = useState("");
  const [gameMode, setGameMode] = useState<GameMode>("freeform");
  const [validation, setValidation] = useState("");
  const [enhanceError, setEnhanceError] = useState("");
  const [enhancing, setEnhancing] = useState(false);
  const locked = busy || enhancing;

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape" && !locked) onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, locked, onClose]);

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const result = promptSchema.safeParse(prompt);
    if (!result.success) { setValidation(result.error.issues[0].message); return; }
    setValidation("");
    onCreate(result.data, gameMode);
  };
  const enhance = async () => {
    const premise = promptSchema.safeParse(prompt);
    if (!premise.success) { setValidation(premise.error.issues[0].message); return; }
    const request = directionSchema.safeParse(direction);
    if (!request.success) { setEnhanceError(request.error.issues[0].message); return; }
    setValidation("");
    setEnhanceError("");
    setEnhancing(true);
    try {
      setPrompt(await onEnhance(premise.data, request.data));
      setDirection("");
    } catch (cause) {
      setEnhanceError(cause instanceof Error ? cause.message : "The world could not be enhanced.");
    } finally { setEnhancing(false); }
  };

  return (
    <AnimatePresence initial={false}>
      {open && <motion.div key="create-world-dialog" className="dialog-scrim" onMouseDown={(event) => { if (event.target === event.currentTarget && !locked) onClose(); }}
        initial={false} exit={{ y: reduceMotion ? 0 : 6 }} transition={{ duration: reduceMotion ? 0 : 0.16, ease: "easeOut" }}>
        <motion.section className="create-dialog" role="dialog" aria-modal="true" aria-labelledby="create-title" aria-describedby="create-description"
          initial={{ y: reduceMotion ? 0 : 12 }} animate={{ y: 0 }} exit={{ y: reduceMotion ? 0 : 8 }}
          transition={{ duration: reduceMotion ? 0 : 0.2, ease: "easeOut" }}>
          <header className="dialog-head">
            <div>
              <p className="section-overline">A NEW CAMPAIGN</p>
              <h2 id="create-title">What is true here?</h2>
            </div>
            <button className="icon-button" aria-label="Close" onClick={onClose} disabled={locked}><X size={18} /></button>
          </header>
          <p id="create-description" className="create-lead">Start with any detail: who you are, what this world allows, or the moment everything changes.</p>
          <form onSubmit={submit}>
            <label className="world-prompt-label" htmlFor="world-prompt">World premise</label>
            <textarea id="world-prompt" autoFocus value={prompt} onChange={(event) => { setPrompt(event.target.value); setValidation(""); }} placeholder="I am Malachar, an immortal demon king. My fortress is quiet until an unexpected delegation arrives…" maxLength={30_000} disabled={locked} />
            <div className="prompt-foot"><span>{prompt.length.toLocaleString()} / 30,000</span><span>Your words become the campaign&apos;s constitution.</span></div>
            <div className="enhance-world">
              <label htmlFor="world-enhancement">What should the model develop?</label>
              <textarea id="world-enhancement" className="enhance-world-input" value={direction} onChange={(event) => { setDirection(event.target.value); setEnhanceError(""); }} maxLength={2_000} disabled={locked} placeholder="Add a motive, a place’s history, or a rule of magic…" />
              <button type="button" className="quiet-button enhance-world-button" onClick={() => void enhance()} disabled={locked}>
                {enhancing ? <LoaderCircle className="spin" size={15} /> : <Sparkles size={15} />}
                <span>{enhancing ? "Developing the world" : "Enhance world"}</span>
              </button>
            </div>
            <label className="world-mode-field" htmlFor="world-game-mode">
              <span>How do you want to play?</span>
              <select id="world-game-mode" value={gameMode} onChange={(event) => setGameMode(event.target.value as GameMode)} disabled={locked}>
                <option value="freeform">Write every action</option>
                <option value="guided">Get choices after each scene</option>
              </select>
              <small>You can switch during the story. You can always type your own action.</small>
            </label>
            {(validation || enhanceError || error) && <p className="form-message form-message--error" role="alert">{validation || enhanceError || error}</p>}
            <footer className="dialog-actions">
              <button type="button" className="quiet-button" onClick={onClose} disabled={locked}>Back</button>
              <button type="submit" className="primary-button" disabled={locked}>
                {busy ? <LoaderCircle className="spin" size={16} /> : <ArrowUpRight size={16} />}
                <span>{busy ? "Setting the world" : "Enter the world"}</span>
              </button>
            </footer>
          </form>
        </motion.section>
      </motion.div>}
    </AnimatePresence>
  );
}
