"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { Lightbulb, ListTodo, NotebookPen, Pin, PinOff, Plus, Quote, Search, Trash2, X } from "lucide-react";
import { FormEvent, useState } from "react";
import { Pager, usePaged } from "@/components/Pager";
import { api, CampaignDetail, CampaignNote, NoteTag } from "@/lib/api";

const TAGS: Array<{ id: NoteTag; label: string; Icon: typeof Quote }> = [
  { id: "note", label: "Note", Icon: NotebookPen }, { id: "clue", label: "Clue", Icon: Search },
  { id: "idea", label: "Idea", Icon: Lightbulb }, { id: "todo", label: "To do", Icon: ListTodo }, { id: "quote", label: "Passage", Icon: Quote },
];
const TAG_ICON = Object.fromEntries(TAGS.map((tag) => [tag.id, tag.Icon])) as Record<NoteTag, typeof Quote>;

/** The player's notebook: private notes and saved passages; pinned ones remind the narrator (never canon). */
export function NotebookPanel({ campaign }: { campaign: CampaignDetail }) {
  const client = useQueryClient();
  const reduceMotion = useReducedMotion();
  const key = ["notes", campaign.id];
  const notes = useQuery({ queryKey: key, queryFn: () => api.notes(campaign.id) });
  const [filter, setFilter] = useState<NoteTag | "all" | "pinned">("all");
  const [search, setSearch] = useState("");
  const [draft, setDraft] = useState<{ title: string; body: string; tag: NoteTag } | null>(null);
  const [error, setError] = useState("");
  const refresh = () => client.invalidateQueries({ queryKey: key });
  const create = useMutation({
    mutationFn: (payload: { title: string; body: string; tag: NoteTag }) => api.createNote(campaign.id, campaign.branch.id, payload),
    onSuccess: () => { setDraft(null); setError(""); void refresh(); },
    onError: (failure) => setError(failure.message),
  });
  const update = useMutation({
    mutationFn: ({ id, ...payload }: { id: string; pinned?: boolean }) => api.updateNote(campaign.id, id, payload),
    onSuccess: () => void refresh(),
  });
  const remove = useMutation({ mutationFn: (id: string) => api.deleteNote(campaign.id, id), onSuccess: () => void refresh() });

  const query = search.trim().toLocaleLowerCase();
  const visible = (notes.data?.notes ?? []).filter((note) => (filter === "all" || (filter === "pinned" ? note.pinned : note.tag === filter))
    && (!query || `${note.title} ${note.body} ${note.quote}`.toLocaleLowerCase().includes(query)));
  const paged = usePaged(visible, 6, `${filter}|${query}`);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (draft && (draft.title.trim() || draft.body.trim())) create.mutate(draft);
  };
  const pinnedCount = (notes.data?.notes ?? []).filter((note) => note.pinned).length;

  return <div className="lore-content notebook-panel">
    <p className="lore-label">YOUR NOTEBOOK</p>
    <h2 className="lore-name">Notebook</h2>
    <p className="lore-copy notebook-lead">Private notes and saved passages. Pin up to six and the narrator keeps them in mind; they never become canon.</p>
    <div className="panel-actions">
      <button type="button" className="btn btn--primary" onClick={() => setDraft(draft ? null : { title: "", body: "", tag: "note" })} aria-expanded={Boolean(draft)}>
        {draft ? <X size={15} /> : <Plus size={15} />}<span>{draft ? "Close" : "New note"}</span></button>
      <span className="panel-meta">{pinnedCount}/6 pinned</span>
    </div>
    <AnimatePresence initial={false}>
      {draft && <motion.form key="draft" className="note-editor" onSubmit={submit}
        initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} transition={{ duration: reduceMotion ? 0 : 0.2 }}>
        <div className="chip-row" role="radiogroup" aria-label="Kind of note">
          {TAGS.filter((tag) => tag.id !== "quote").map(({ id, label, Icon }) => <button type="button" key={id} role="radio" aria-checked={draft.tag === id}
            className="chip" onClick={() => setDraft({ ...draft, tag: id })}><Icon size={13} />{label}</button>)}
        </div>
        <input aria-label="Title" placeholder="Title" value={draft.title} maxLength={160} onChange={(event) => setDraft({ ...draft, title: event.target.value })} autoFocus />
        <textarea aria-label="Note" placeholder="What do you want to remember?" rows={4} value={draft.body} maxLength={8000}
          onChange={(event) => setDraft({ ...draft, body: event.target.value })} />
        {error && <p className="field-help field-help--error" role="alert">{error}</p>}
        <div className="note-editor-actions"><button type="submit" className="btn btn--primary" disabled={create.isPending || !(draft.title.trim() || draft.body.trim())}>
          {create.isPending ? "Saving…" : "Save note"}</button></div>
      </motion.form>}
    </AnimatePresence>
    <div className="notebook-tools">
      <input type="search" className="panel-search" placeholder="Search notes" value={search} onChange={(event) => setSearch(event.target.value)} aria-label="Search notes" />
      <div className="chip-row" aria-label="Filter notes">
        {(["all", "pinned", ...TAGS.map((tag) => tag.id)] as const).map((id) => <button type="button" key={id} className="chip" aria-pressed={filter === id}
          onClick={() => setFilter(id)}>{id === "all" ? "All" : id === "pinned" ? "Pinned" : TAGS.find((tag) => tag.id === id)?.label}</button>)}
      </div>
    </div>
    {notes.isLoading && <p className="lore-copy">Opening your notebook…</p>}
    {notes.isSuccess && !visible.length && <div className="empty-card"><NotebookPen size={20} /><p>{notes.data.notes.length
      ? "No notes match." : "Nothing written yet. Add a note, or use Save on any passage in the story."}</p></div>}
    <ul className="note-list">
      <AnimatePresence initial={false}>
        {paged.items.map((note) => <NoteCard key={note.id} note={note} reduceMotion={Boolean(reduceMotion)}
          canPin={note.pinned || pinnedCount < 6}
          onPin={() => update.mutate({ id: note.id, pinned: !note.pinned })}
          onDelete={() => { if (window.confirm("Delete this note?")) remove.mutate(note.id); }} />)}
      </AnimatePresence>
    </ul>
    <Pager {...paged} label="Notes" />
  </div>;
}

function NoteCard({ note, onPin, onDelete, canPin, reduceMotion }: {
  note: CampaignNote; onPin: () => void; onDelete: () => void; canPin: boolean; reduceMotion: boolean;
}) {
  const Icon = TAG_ICON[note.tag] ?? NotebookPen;
  return <motion.li layout={!reduceMotion} className={`note-card${note.pinned ? " is-pinned" : ""}`} data-tag={note.tag}
    initial={{ opacity: 0, y: reduceMotion ? 0 : 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, scale: reduceMotion ? 1 : 0.97 }}>
    <header><span className="note-tag"><Icon size={12} />{TAGS.find((tag) => tag.id === note.tag)?.label ?? "Note"}
      {note.turn_index != null && <em>· turn {note.turn_index}</em>}</span>
      <span className="note-actions">
        <button type="button" className="icon-button icon-button--sm" onClick={onPin} disabled={!canPin} aria-pressed={note.pinned}
          aria-label={note.pinned ? "Unpin" : "Pin for the narrator"} title={canPin ? (note.pinned ? "Unpin" : "Pin: the narrator keeps it in mind") : "Six notes are already pinned"}>
          {note.pinned ? <PinOff size={14} /> : <Pin size={14} />}</button>
        <button type="button" className="icon-button icon-button--sm" onClick={onDelete} aria-label="Delete note" title="Delete"><Trash2 size={14} /></button>
      </span></header>
    {note.title && <strong>{note.title}</strong>}
    {note.quote && <blockquote>{note.quote}</blockquote>}
    {note.body && <p>{note.body}</p>}
  </motion.li>;
}
