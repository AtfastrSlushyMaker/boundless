"use client";

import { Archive, Copy, Download, MoreHorizontal, Pencil, Trash2, Undo2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { CampaignCard, exportCampaign } from "@/lib/api";

type Props = { campaign: CampaignCard; onRename: (campaign: CampaignCard) => void; onArchive: (campaign: CampaignCard) => void; onDuplicate: (campaign: CampaignCard) => void; onDelete: (campaign: CampaignCard) => void; onError: (error: string) => void };

export function WorldMenu({ campaign, onRename, onArchive, onDuplicate, onDelete, onError }: Props) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    root.current?.querySelector<HTMLButtonElement>('[role="menuitem"]')?.focus();
    const close = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") { setOpen(false); trigger.current?.focus(); }
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("pointerdown", close); document.removeEventListener("keydown", onKey); };
  }, [open]);
  const runExport = async () => {
    setOpen(false);
    try { await exportCampaign(campaign.id, campaign.title); }
    catch (error) { onError(error instanceof Error ? error.message : "Export failed."); }
  };
  return (
    <div className="world-menu" ref={root}>
      <button ref={trigger} type="button" className="icon-button menu-trigger" aria-label={`Actions for ${campaign.title}`} aria-expanded={open} aria-haspopup="menu" onClick={() => setOpen(!open)}><MoreHorizontal size={19} /></button>
      {open && <div className="menu-popover" role="menu" aria-label={`Actions for ${campaign.title}`} onKeyDown={(event) => {
        const options = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('button[role="menuitem"]'));
        const index = options.indexOf(document.activeElement as HTMLButtonElement);
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault();
          options[(index + (event.key === "ArrowDown" ? 1 : options.length - 1)) % options.length]?.focus();
        }
      }}>
        <button type="button" role="menuitem" onClick={() => { setOpen(false); onRename(campaign); }}><Pencil size={15} />Rename</button>
        <button type="button" role="menuitem" onClick={() => { setOpen(false); onDuplicate(campaign); }}><Copy size={15} />Duplicate</button>
        <button type="button" role="menuitem" onClick={runExport}><Download size={15} />Export</button>
        <button type="button" role="menuitem" onClick={() => { setOpen(false); onArchive(campaign); }}>{campaign.archived ? <Undo2 size={15} /> : <Archive size={15} />}{campaign.archived ? "Restore" : "Archive"}</button>
        <div className="menu-separator" />
        <button type="button" className="menu-danger" role="menuitem" onClick={() => { setOpen(false); onDelete(campaign); }}><Trash2 size={15} />Delete</button>
      </div>}
    </div>
  );
}
