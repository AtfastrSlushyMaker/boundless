"use client";

import { Archive, Copy, Download, MoreHorizontal, Pencil, Trash2, Undo2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { CampaignCard, exportCampaign } from "@/lib/api";

type Props = { campaign: CampaignCard; onRename: (campaign: CampaignCard) => void; onArchive: (campaign: CampaignCard) => void; onDuplicate: (campaign: CampaignCard) => void; onDelete: (campaign: CampaignCard) => void; onError: (error: string) => void };

export function WorldMenu({ campaign, onRename, onArchive, onDuplicate, onDelete, onError }: Props) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const close = (event: MouseEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  const runExport = async () => {
    setOpen(false);
    try { await exportCampaign(campaign.id, campaign.title); }
    catch (error) { onError(error instanceof Error ? error.message : "Export failed."); }
  };
  return (
    <div className="world-menu" ref={root}>
      <button className="icon-button menu-trigger" aria-label={`Actions for ${campaign.title}`} aria-expanded={open} onClick={() => setOpen(!open)}><MoreHorizontal size={19} /></button>
      {open && <div className="menu-popover" role="menu">
        <button role="menuitem" onClick={() => { setOpen(false); onRename(campaign); }}><Pencil size={15} />Rename</button>
        <button role="menuitem" onClick={() => { setOpen(false); onDuplicate(campaign); }}><Copy size={15} />Duplicate</button>
        <button role="menuitem" onClick={runExport}><Download size={15} />Export</button>
        <button role="menuitem" onClick={() => { setOpen(false); onArchive(campaign); }}>{campaign.archived ? <Undo2 size={15} /> : <Archive size={15} />}{campaign.archived ? "Restore" : "Archive"}</button>
        <div className="menu-separator" />
        <button className="menu-danger" role="menuitem" onClick={() => { setOpen(false); onDelete(campaign); }}><Trash2 size={15} />Delete</button>
      </div>}
    </div>
  );
}
