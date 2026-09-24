"use client";

import { useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, motion, useIsPresent, useReducedMotion } from "motion/react";
import { AlertTriangle, ArrowLeft, Bookmark, Check, GitBranch, Pencil, RotateCcw, X } from "lucide-react";
import { FormEvent, KeyboardEvent, ReactNode, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api, CampaignDetail } from "@/lib/api";

type Dialog = "rewrite" | "branch" | "rewind";
const DIRECTIONS = ["More detail", "Shorter", "More dialogue", "Slower pace", "Raise the stakes", "A different outcome"];

type Props = {
  campaign: CampaignDetail; turnId: string; turnIndex: number; latestIndex: number; branchId: string; content: string;
  disabled?: boolean; onChanged: (detail: CampaignDetail) => void; onError: (message: string) => void;
};

/** Edit, rewrite, branch, rewind, and save for one passage, with the consequences spelled out before anything changes. */
export function TurnActions({ campaign, turnId, turnIndex, latestIndex, branchId, content, disabled, onChanged, onError }: Props) {
  const client = useQueryClient();
  const reduceMotion = useReducedMotion();
  const later = Math.max(0, latestIndex - turnIndex);
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const [editing, setEditing] = useState(false);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);

  const savePassage = async () => {
    const selection = window.getSelection()?.toString().trim() ?? "";
    const quote = (selection && content.includes(selection.slice(0, 40)) ? selection : content).slice(0, 3900);
    try {
      await api.createNote(campaign.id, branchId, { quote, turn_id: turnId, tag: "quote" });
      setSaved(true);
      void client.invalidateQueries({ queryKey: ["notes", campaign.id] });
      window.setTimeout(() => setSaved(false), 6000);
    } catch (error) { onError(error instanceof Error ? error.message : "The passage could not be saved."); }
  };

  if (editing) return <TurnEditor turnId={turnId} turnIndex={turnIndex} later={later} content={content}
    onCancel={() => setEditing(false)} onSaved={(detail) => { setEditing(false); onChanged(detail); }} onError={onError} />;

  const off = disabled || busy;
  return <>
    <div className={`turn-tools${turnIndex === latestIndex ? " is-latest" : ""}`} role="toolbar" aria-label={`Turn ${turnIndex} actions`}>
      <div className="turn-tool-group">
        <ToolButton icon={<Pencil size={14} />} label="Edit" hint="Correct or change this passage" disabled={off} onClick={() => setEditing(true)} />
        <ToolButton icon={<RotateCcw size={14} />} label="Rewrite" hint="Ask the narrator for a new version" disabled={off} onClick={() => setDialog("rewrite")} />
      </div>
      <div className="turn-tool-group">
        <ToolButton icon={<GitBranch size={14} />} label="Branch" hint="Start a new timeline from here; this one stays as it is" disabled={off} onClick={() => setDialog("branch")} />
        {later > 0 && <ToolButton icon={<ArrowLeft size={14} />} label="Rewind" hint={`Return the story to this turn (${later} later ${later === 1 ? "turn leaves" : "turns leave"})`} disabled={off} onClick={() => setDialog("rewind")} />}
      </div>
      <div className="turn-tool-group">
        <ToolButton icon={saved ? <Check size={14} /> : <Bookmark size={14} />} label={saved ? "Saved · Open" : "Save"} done={saved}
          hint={saved ? "Saved to your notebook. Click to open it" : "Save this passage, or the text you selected in it, to your notebook"} disabled={disabled}
          onClick={() => saved ? window.dispatchEvent(new CustomEvent("boundless:open-panel", { detail: { panel: "notebook" } })) : void savePassage()} />
      </div>
    </div>
    {typeof document !== "undefined" && createPortal(<AnimatePresence>
      {dialog && <Scrim key={dialog} onDismiss={() => { if (!busy) setDialog(null); }} fast={Boolean(reduceMotion)}>
        <motion.section className="prompt-dialog turn-dialog" role="dialog" aria-modal="true" aria-labelledby={`turn-dialog-${turnId}`}
          initial={{ y: reduceMotion ? 0 : 12, scale: reduceMotion ? 1 : 0.985 }} animate={{ y: 0, scale: 1 }} exit={{ y: reduceMotion ? 0 : 6 }}
          transition={{ duration: reduceMotion ? 0 : 0.2, ease: [0.22, 1, 0.36, 1] }}>
          {dialog === "rewrite" && <RewriteForm turnIndex={turnIndex} later={later} latestIndex={latestIndex} titleId={`turn-dialog-${turnId}`}
            onClose={() => setDialog(null)} onBranchInstead={() => setDialog("branch")}
            onSubmit={(instruction) => {
              window.dispatchEvent(new CustomEvent("boundless:regenerate", { detail: { turnId, instruction, branchId } }));
              setDialog(null);
            }} />}
          {dialog === "branch" && <BranchForm turnIndex={turnIndex} titleId={`turn-dialog-${turnId}`} busy={busy} onClose={() => setDialog(null)}
            onSubmit={async (name) => {
              setBusy(true);
              try {
                const branch = await api.createBranch(campaign.id, name, branchId, turnId);
                await api.activateBranch(campaign.id, branch.id);
                window.dispatchEvent(new CustomEvent("boundless:branch", { detail: { branchId: branch.id } }));
                setDialog(null);
              } catch (error) { onError(error instanceof Error ? error.message : "The branch could not be created."); }
              finally { setBusy(false); }
            }} />}
          {dialog === "rewind" && <RewindForm turnIndex={turnIndex} later={later} latestIndex={latestIndex} titleId={`turn-dialog-${turnId}`}
            onClose={() => setDialog(null)} onBranchInstead={() => setDialog("branch")}
            onSubmit={() => {
              window.dispatchEvent(new CustomEvent("boundless:rewind", { detail: { turnId, branchId } }));
              setDialog(null);
            }} />}
        </motion.section>
      </Scrim>}
    </AnimatePresence>, document.body)}
  </>;
}

/** A dialog backdrop that stops catching clicks the moment it starts to close. */
function Scrim({ children, onDismiss, fast }: { children: ReactNode; onDismiss: () => void; fast: boolean }) {
  const present = useIsPresent();
  return <motion.div className="dialog-scrim" style={{ pointerEvents: present ? "auto" : "none" }}
    onMouseDown={(event) => { if (present && event.target === event.currentTarget) onDismiss(); }}
    initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: fast ? 0 : 0.16 }}>{children}</motion.div>;
}

function ToolButton({ icon, label, hint, onClick, disabled, done }: { icon: ReactNode; label: string; hint: string; onClick: () => void; disabled?: boolean; done?: boolean }) {
  return <button type="button" className={`tool-button${done ? " is-done" : ""}`} onClick={onClick} disabled={disabled} title={hint} aria-label={`${label}: ${hint}`}>
    {icon}<span>{label}</span></button>;
}

function DialogHead({ overline, title, titleId, onClose, disabled }: { overline: string; title: string; titleId: string; onClose: () => void; disabled?: boolean }) {
  useEffect(() => {
    const onKey = (event: globalThis.KeyboardEvent) => { if (event.key === "Escape" && !disabled) { event.stopPropagation(); onClose(); } };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose, disabled]);
  return <header className="dialog-head">
    <div><p className="section-overline">{overline}</p><h2 id={titleId}>{title}</h2></div>
    <button type="button" className="icon-button" aria-label="Close" onClick={onClose} disabled={disabled}><X size={18} /></button>
  </header>;
}

function LaterTurnsNote({ turnIndex, later, latestIndex, verb, onBranchInstead }: { turnIndex: number; later: number; latestIndex: number; verb: string; onBranchInstead: () => void }) {
  if (!later) return null;
  return <div className="consequence-note" role="note">
    <AlertTriangle size={16} aria-hidden="true" />
    <p><strong>{later === 1 ? `Turn ${latestIndex}` : `Turns ${turnIndex + 1}–${latestIndex}`} will leave this timeline</strong> when you {verb}.
      They stay in the archive, but the story continues from here. <button type="button" className="inline-link" onClick={onBranchInstead}>Branch instead</button> to keep them.</p>
  </div>;
}

function RewriteForm({ turnIndex, later, latestIndex, titleId, onClose, onSubmit, onBranchInstead }: {
  turnIndex: number; later: number; latestIndex: number; titleId: string; onClose: () => void; onSubmit: (instruction: string) => void; onBranchInstead: () => void;
}) {
  const [picked, setPicked] = useState<string[]>([]);
  const [text, setText] = useState("");
  const submit = (event: FormEvent) => {
    event.preventDefault();
    onSubmit([picked.length ? `${picked.join(". ")}.` : "", text.trim()].filter(Boolean).join(" "));
  };
  return <>
    <DialogHead overline={`TURN ${turnIndex}`} title="Rewrite this passage" titleId={titleId} onClose={onClose} />
    <p className="create-lead">The narrator answers your same action again. Pick a direction, add your own, or leave both empty for a fresh take.</p>
    <form onSubmit={submit} className="turn-dialog-form">
      <div className="chip-row" role="group" aria-label="Directions">
        {DIRECTIONS.map((direction) => <button type="button" key={direction} className="chip" aria-pressed={picked.includes(direction)}
          onClick={() => setPicked(picked.includes(direction) ? picked.filter((entry) => entry !== direction) : [...picked, direction])}>{direction}</button>)}
      </div>
      <label className="prompt-field-label" htmlFor={`${titleId}-text`}>Anything specific <span>optional</span></label>
      <textarea id={`${titleId}-text`} autoFocus rows={3} maxLength={2000} value={text} onChange={(event) => setText(event.target.value)}
        placeholder="Keep the same scene, but let the envoy reveal why she came…" />
      <LaterTurnsNote turnIndex={turnIndex} later={later} latestIndex={latestIndex} verb="rewrite this turn" onBranchInstead={onBranchInstead} />
      <footer className="dialog-actions">
        <button type="button" className="btn btn--ghost" onClick={onClose}>Cancel</button>
        <button type="submit" className="btn btn--primary"><RotateCcw size={15} />Rewrite</button>
      </footer>
    </form>
  </>;
}

function BranchForm({ turnIndex, titleId, busy, onClose, onSubmit }: { turnIndex: number; titleId: string; busy: boolean; onClose: () => void; onSubmit: (name: string) => void }) {
  const [name, setName] = useState("");
  const submit = (event: FormEvent) => { event.preventDefault(); if (name.trim()) onSubmit(name.trim()); };
  return <>
    <DialogHead overline={`FROM TURN ${turnIndex}`} title="Branch the story" titleId={titleId} onClose={onClose} disabled={busy} />
    <div className="branch-diagram" aria-hidden="true">
      <span className="branch-line" /><span className="branch-dot">{turnIndex}</span><span className="branch-fork" />
      <em>this timeline stays as it is</em><strong>your new timeline</strong>
    </div>
    <form onSubmit={submit} className="turn-dialog-form">
      <label className="prompt-field-label" htmlFor={`${titleId}-name`}>Name the new timeline</label>
      <input id={`${titleId}-name`} autoFocus maxLength={120} value={name} onChange={(event) => setName(event.target.value)} disabled={busy}
        placeholder="What if I trusted the envoy…" />
      <p className="field-help">You can switch between timelines any time from the thread menu at the top.</p>
      <footer className="dialog-actions">
        <button type="button" className="btn btn--ghost" onClick={onClose} disabled={busy}>Cancel</button>
        <button type="submit" className="btn btn--primary" disabled={busy || !name.trim()}><GitBranch size={15} />{busy ? "Creating…" : "Create and switch"}</button>
      </footer>
    </form>
  </>;
}

function RewindForm({ turnIndex, later, latestIndex, titleId, onClose, onSubmit, onBranchInstead }: {
  turnIndex: number; later: number; latestIndex: number; titleId: string; onClose: () => void; onSubmit: () => void; onBranchInstead: () => void;
}) {
  return <>
    <DialogHead overline="REWIND" title={`Return to turn ${turnIndex}?`} titleId={titleId} onClose={onClose} />
    <p className="create-lead">The story, people, items and places go back to how they were at the end of turn {turnIndex}.</p>
    <div className="rewind-strip" aria-hidden="true">
      <span className="is-kept">{turnIndex}</span>
      {Array.from({ length: Math.min(later, 6) }, (_, index) => <span key={index} className="is-cut">{turnIndex + index + 1}</span>)}
      {later > 6 && <span className="is-cut is-more">+{later - 6}</span>}
    </div>
    <LaterTurnsNote turnIndex={turnIndex} later={later} latestIndex={latestIndex} verb="rewind" onBranchInstead={onBranchInstead} />
    <footer className="dialog-actions">
      <button type="button" className="btn btn--ghost" onClick={onClose}>Cancel</button>
      <button type="button" className="btn btn--secondary" onClick={onBranchInstead}><GitBranch size={15} />Branch instead</button>
      <button type="button" className="btn btn--danger" onClick={onSubmit} autoFocus><ArrowLeft size={15} />Rewind</button>
    </footer>
  </>;
}

function TurnEditor({ turnId, turnIndex, later, content, onCancel, onSaved, onError }: {
  turnId: string; turnIndex: number; later: number; content: string; onCancel: () => void;
  onSaved: (detail: CampaignDetail) => void; onError: (message: string) => void;
}) {
  const [draft, setDraft] = useState(content);
  const [mode, setMode] = useState<"wording" | "story">(later > 0 ? "wording" : "story");
  const [busy, setBusy] = useState(false);
  const ref = useRef<HTMLTextAreaElement>(null);
  const changed = draft.trim() !== content.trim();
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight + 2, window.innerHeight * 0.6)}px`;
  }, [draft]);
  useEffect(() => { ref.current?.focus(); }, []);
  const cancel = () => { if (!changed || window.confirm("Discard your changes to this passage?")) onCancel(); };
  const save = async () => {
    if (!changed || busy) return;
    setBusy(true); onError("");
    try { onSaved(await api.editTurn(turnId, draft, mode === "wording")); }
    catch (error) { onError(error instanceof Error ? error.message : "The edit could not be saved."); }
    finally { setBusy(false); }
  };
  const onKey = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) { event.preventDefault(); void save(); }
    if (event.key === "Escape") { event.preventDefault(); cancel(); }
  };
  const words = draft.trim() ? draft.trim().split(/\s+/).length : 0;
  return <motion.div className="turn-editor" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}>
    <header><span><Pencil size={13} />Editing turn {turnIndex}</span>
      <button type="button" className="icon-button icon-button--sm" onClick={cancel} aria-label="Close editor"><X size={15} /></button></header>
    <textarea ref={ref} id={`edit-${turnId}`} aria-label={`Narration for turn ${turnIndex}`} value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={onKey} disabled={busy} />
    <div className="turn-editor-mode" role="radiogroup" aria-label="What this edit does">
      <button type="button" role="radio" aria-checked={mode === "wording"} onClick={() => setMode("wording")}>
        <strong>Fix the wording</strong><small>Only the text changes. People, items and later turns stay as they are.</small></button>
      <button type="button" role="radio" aria-checked={mode === "story"} onClick={() => setMode("story")}>
        <strong>Change what happened</strong><small>{later ? `The world is re-read from this text, and ${later === 1 ? "the turn after it leaves" : `the ${later} turns after it leave`} this timeline.`
          : "The world is re-read from this text: people, items, places and relationships update."}</small></button>
    </div>
    <footer>
      <span className="turn-editor-meta">{words} words{changed ? " · edited" : ""} · <kbd>⌘</kbd><kbd>↵</kbd> to save</span>
      <button type="button" className="btn btn--ghost" onClick={cancel} disabled={busy}>Cancel</button>
      <button type="button" className="btn btn--primary" onClick={() => void save()} disabled={busy || !changed}>
        {busy ? (mode === "story" ? "Re-reading the world…" : "Saving…") : "Save passage"}</button>
    </footer>
  </motion.div>;
}
