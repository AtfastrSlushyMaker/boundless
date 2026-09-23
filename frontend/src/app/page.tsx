"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { motion, useReducedMotion } from "motion/react";
import { ArrowDownLeft, ArrowUpRight, BookOpenText, Check, CircleAlert, Command, Import, LoaderCircle, Settings2, WifiOff } from "lucide-react";
import { FormEvent, useEffect, useRef, useState } from "react";
import { AtlasArtwork } from "@/components/AtlasArtwork";
import { CreateWorldDialog } from "@/components/CreateWorldDialog";
import { ModelSettingsDialog } from "@/components/ModelSettingsDialog";
import { WorldMenu } from "@/components/WorldMenu";
import { api, CampaignCard, importCampaign } from "@/lib/api";

function timeAgo(value: string) {
  const time = new Date(value).getTime();
  if (!Number.isFinite(time)) return "Not played yet";
  const hours = Math.max(0, Math.floor((Date.now() - time) / 3_600_000));
  if (hours < 1) return "Just now";
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return days < 7 ? `${days}d ago` : new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(new Date(value));
}

export default function HomePage() {
  const router = useRouter();
  const reduceMotion = useReducedMotion();
  const cache = useQueryClient();
  const [createOpen, setCreateOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [showArchived, setShowArchived] = useState(false);
  const [importError, setImportError] = useState("");
  const [notice, setNotice] = useState("");
  const [worldAction, setWorldAction] = useState<{ kind: "rename" | "delete"; campaign: CampaignCard } | null>(null);
  const [worldName, setWorldName] = useState("");
  const importInput = useRef<HTMLInputElement>(null);
  const worlds = useQuery({ queryKey: ["campaigns", showArchived], queryFn: () => api.campaigns(showArchived) });
  const health = useQuery({ queryKey: ["health"], queryFn: api.health, refetchInterval: 20_000 });
  const create = useMutation({
    mutationFn: api.createCampaign,
    onSuccess: (campaign) => {
      setCreateOpen(false);
      router.push(`/campaign/${campaign.id}?branch=${campaign.branch.id}`);
    },
  });
  const refreshWorlds = () => cache.invalidateQueries({ queryKey: ["campaigns"] });
  const runAction = async (label: string, work: () => Promise<unknown>) => {
    setNotice("");
    try { await work(); await refreshWorlds(); setNotice(label); }
    catch (error) { setNotice(error instanceof Error ? error.message : "That action failed."); }
  };
  const onRename = (campaign: CampaignCard) => { setWorldName(campaign.title); setWorldAction({ kind: "rename", campaign }); };
  const onDelete = (campaign: CampaignCard) => setWorldAction({ kind: "delete", campaign });
  const submitWorldAction = (event: FormEvent) => {
    event.preventDefault();
    if (!worldAction) return;
    const { kind, campaign } = worldAction;
    const title = worldName.trim();
    if (kind === "rename" && !title) return;
    setWorldAction(null);
    void runAction(kind === "rename" ? "World renamed." : "World deleted.", () =>
      kind === "rename" ? api.renameCampaign(campaign.id, title) : api.deleteCampaign(campaign.id));
  };
  useEffect(() => {
    if (!worldAction) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") setWorldAction(null); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [worldAction]);
  const onArchive = (campaign: CampaignCard) => void runAction(campaign.archived ? "World restored." : "World archived.", () => api.archiveCampaign(campaign.id, !campaign.archived));
  const onDuplicate = (campaign: CampaignCard) => void runAction("World duplicated.", async () => {
    const copy = await api.duplicateCampaign(campaign.id);
    router.push(`/campaign/${copy.id}?branch=${copy.branch.id}`);
  });
  const onImport = async (file?: File) => {
    if (!file) return;
    setImportError("");
    try {
      const campaign = await importCampaign(file);
      await refreshWorlds();
      router.push(`/campaign/${campaign.id}?branch=${campaign.branch.id}`);
    } catch (error) {
      setImportError(error instanceof Error ? error.message : "Campaign import failed.");
    } finally {
      if (importInput.current) importInput.current.value = "";
    }
  };

  const status = health.data;
  const modelConnected = status?.model?.status === "connected";
  const databaseConnected = status?.database === "connected";

  return (
    <main className="home-shell theme-dark_fantasy">
      <header className="topbar home-topbar">
        <Link href="/" className="brand-lockup" aria-label="Boundless home">
          <span className="brand-glyph" aria-hidden="true">B</span><span>Boundless</span>
        </Link>
        <nav className="home-navigation" aria-label="Home navigation"><a href="#worlds">Your worlds</a></nav>
        <div className="topbar-right">
          <button className="connection-indicator" onClick={() => setSettingsOpen(true)} aria-label="Open local model settings">
            <span className={`status-mark ${modelConnected ? "status-mark--on" : "status-mark--off"}`} />
            <span>{modelConnected ? "Model ready" : status?.model?.status === "offline" ? "Model offline" : status?.model?.status === "loading" ? "Model loading" : "Checking model"}</span>
          </button>
          <button className="icon-button top-settings" onClick={() => setSettingsOpen(true)} aria-label="Open settings"><Settings2 size={18} /></button>
        </div>
      </header>

      <section className="atlas-hero" aria-labelledby="home-title">
        <AtlasArtwork />
        <div className="hero-coastline">
          <div className="hero-copy">
            <h1 id="home-title">A world that<br /><em>answers back.</em></h1>
            <p className="hero-description">Your words set the laws. Your choices decide what comes next.</p>
            <button className="primary-button hero-cta" onClick={() => { create.reset(); setCreateOpen(true); }}>
              <span>Begin a world</span><ArrowUpRight size={17} />
            </button>
          </div>
          <div className="map-caption"><span>VAELORIA · CARTOGRAPHER&apos;S DRAFT</span><span>44° N&nbsp; / &nbsp;18° E</span></div>
        </div>
        <button className="hero-scroll" onClick={() => document.getElementById("worlds")?.scrollIntoView({ behavior: "smooth" })} aria-label="Scroll to your worlds">
          <span>YOUR WORLDS</span><ArrowDownLeft size={15} />
        </button>
      </section>

      <section className="worlds-section" id="worlds" aria-labelledby="worlds-title">
        <div className="section-heading">
          <div><p className="section-overline">CAMPAIGN ARCHIVE</p><h2 id="worlds-title">Your worlds</h2></div>
          <div className="archive-actions">
            <button className="quiet-button import-button" onClick={() => importInput.current?.click()}><Import size={15} /><span>Import</span></button>
            <input ref={importInput} className="sr-only" type="file" accept=".json,.boundless.json,application/json" onChange={(event) => void onImport(event.target.files?.[0])} />
            <button className="quiet-button archive-toggle" onClick={() => setShowArchived(!showArchived)}>{showArchived ? "Hide archive" : "Show archive"}</button>
          </div>
        </div>
        {notice && <p className="notice-line" role="status"><Check size={14} />{notice}</p>}
        {importError && <p className="notice-line notice-line--error" role="alert"><CircleAlert size={14} />{importError}</p>}
        {worlds.isLoading && <div className="loading-line"><LoaderCircle className="spin" size={16} />Opening your archive…</div>}
        {worlds.isError && <div className="service-note" role="alert"><WifiOff size={18} /><div><strong>Campaign storage is unreachable.</strong><p>Start PostgreSQL with <code>docker compose up -d --wait</code>, then refresh this page.</p></div></div>}
        {worlds.data?.length === 0 && <div className="empty-worlds"><BookOpenText size={19} /><p>{showArchived ? "No archived worlds." : "No worlds yet. The first sentence is yours."}</p><button className="text-button" onClick={() => setCreateOpen(true)}>Begin a world <ArrowUpRight size={14} /></button></div>}
        {!!worlds.data?.length && <div className="world-list">
          {worlds.data.map((campaign, index) => (
            <motion.article className={`world-row theme-${campaign.theme?.family ?? "neutral"}`} key={campaign.id}
              initial={{ y: reduceMotion ? 0 : 7 }} animate={{ y: 0 }}
              transition={{ duration: reduceMotion ? 0 : 0.22, delay: reduceMotion ? 0 : Math.min(index * 0.025, 0.2), ease: "easeOut" }}>
              <div className="world-index">{String(index + 1).padStart(2, "0")}</div>
              <div className="world-main">
                <div className="world-title-line"><h3>{campaign.title}</h3>{campaign.archived && <span className="archive-word">ARCHIVED</span>}</div>
                <p className="world-premise">{campaign.premise || "A world waiting to be remembered."}</p>
                <p className="world-details"><span>{campaign.protagonist_name}</span><span>{campaign.genre}</span>{campaign.current_location && <span>{campaign.current_location}</span>}</p>
              </div>
              <div className="world-meta"><strong>{campaign.turn_count} {campaign.turn_count === 1 ? "turn" : "turns"}</strong><span>{timeAgo(campaign.last_played)}</span></div>
              <Link className="world-resume" href={`/campaign/${campaign.id}?branch=${campaign.branch_id ?? ""}`} aria-label={`Continue ${campaign.title}`}>
                <span>{campaign.turn_count ? "Continue" : "Open"}</span><ArrowUpRight size={16} />
              </Link>
              <WorldMenu campaign={campaign} onRename={onRename} onArchive={onArchive} onDuplicate={onDuplicate} onDelete={onDelete} onError={setNotice} />
            </motion.article>
          ))}
        </div>}
        <footer className="archive-footer">
          <span><Command size={13} /> STORED LOCALLY</span>
          <span>{status?.model?.selection === "deepseek" ? "Story context is sent to DeepSeek when you play." : "Campaigns stay on this device."}</span>
          {!databaseConnected && <span className="offline-note">Database unavailable</span>}
        </footer>
      </section>
      <CreateWorldDialog open={createOpen} busy={create.isPending} error={create.error?.message} onClose={() => setCreateOpen(false)} onCreate={(prompt, game_mode, details) => create.mutate({ prompt, game_mode, ...details })} onEnhance={async (prompt, direction) => (await api.enhanceWorld(prompt, direction)).prompt} />
      <ModelSettingsDialog open={settingsOpen} onClose={() => setSettingsOpen(false)} />
      {worldAction && <div className="dialog-scrim" onMouseDown={(event) => { if (event.target === event.currentTarget) setWorldAction(null); }}>
        <section className="world-action-dialog" role="dialog" aria-modal="true" aria-labelledby="world-action-title">
          <header className="dialog-head"><h2 id="world-action-title">{worldAction.kind === "rename" ? "Rename this world" : "Delete this world?"}</h2>
            <button type="button" className="icon-button" aria-label="Close" onClick={() => setWorldAction(null)}>×</button></header>
          <form onSubmit={submitWorldAction}>
            {worldAction.kind === "rename" ? <label htmlFor="world-action-name">World name<input id="world-action-name" autoFocus maxLength={160} value={worldName} onChange={(event) => setWorldName(event.target.value)} /></label>
              : <p>“{worldAction.campaign.title}” and all its timelines will be removed permanently.</p>}
            <footer className="dialog-actions"><button type="button" className="quiet-button" onClick={() => setWorldAction(null)}>Cancel</button>
              <button type="submit" className={worldAction.kind === "delete" ? "danger-button" : "primary-button"} disabled={worldAction.kind === "rename" && !worldName.trim()}>{worldAction.kind === "rename" ? "Save name" : "Delete world"}</button></footer>
          </form>
        </section>
      </div>}
    </main>
  );
}
