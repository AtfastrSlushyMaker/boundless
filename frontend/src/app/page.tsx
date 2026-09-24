"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { motion, useReducedMotion } from "motion/react";
import { Archive, ArrowDownLeft, ArrowUpRight, BookOpenText, Check, CircleAlert, Command, Import, LoaderCircle, Plus, Search, Settings2, WifiOff } from "lucide-react";
import { FormEvent, useEffect, useRef, useState } from "react";
import { AtlasArtwork } from "@/components/AtlasArtwork";
import { CreateWorldDialog } from "@/components/CreateWorldDialog";
import { ModelSettingsDialog } from "@/components/ModelSettingsDialog";
import { SchemeToggle } from "@/components/SchemeToggle";
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

const MOOD_NAME: Record<string, string> = {
  dark_fantasy: "Dark fantasy", horror: "Horror", mystery: "Mystery", cozy: "Cozy", romance: "Romance", cyberpunk: "Cyberpunk",
  sci_fi: "Science fiction", survival: "Survival", modern: "Modern", neutral: "Open world",
};

/** The hero's name, recovered from "My name is Skander" when the saved name is just "You". */
function heroName(campaign: CampaignCard) {
  if (campaign.protagonist_name && campaign.protagonist_name !== "You") return campaign.protagonist_name;
  return `${campaign.title} ${campaign.premise}`.match(/\b(?:[Mm]y name is|I am)\s+([A-Z][\w'’-]+)/)?.[1] ?? campaign.protagonist_name;
}

/** Titles made from the opening line ("My name is Skander…") read better as "Skander's world". */
function worldTitle(campaign: CampaignCard) {
  const title = campaign.title.trim();
  if (/^(?:my name is|i am|i'm|you are)\b/i.test(title)) {
    const named = title.match(/^(?:[Mm]y name is|I am|I'm)\s+([A-Z][\w'’-]+)/)?.[1];
    const hero = heroName(campaign) !== "You" ? heroName(campaign) : named;
    if (hero) return `${hero}'s world`;
  }
  return title || "Untitled world";
}

/** The premise without its self-introduction, cut at a sentence where possible. */
function excerpt(premise: string) {
  const text = (premise || "").replace(/^\s*(?:my name is|i am|i'm)\s+[^.]{1,40}\.\s*/i, "").trim();
  if (!text) return "A world waiting to be remembered.";
  const sentence = text.match(/^.{40,180}?[.!?](?=\s|$)/)?.[0];
  return sentence ?? (text.length > 170 ? `${text.slice(0, 168).trimEnd()}…` : text);
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
  const [worldSearch, setWorldSearch] = useState("");
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 24);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);
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
      <header className={`topbar home-topbar${scrolled ? " is-scrolled" : ""}`}>
        <Link href="/" className="brand-lockup" aria-label="Boundless home">
          <span className="brand-glyph" aria-hidden="true">B</span><span>Boundless</span>
        </Link>
        <nav className="home-navigation" aria-label="Home navigation"><a href="#worlds">Your worlds</a></nav>
        <div className="topbar-right">
          <button className="connection-indicator" onClick={() => setSettingsOpen(true)} aria-label="Open local model settings">
            <span className={`status-mark ${modelConnected ? "status-mark--on" : "status-mark--off"}`} />
            <span>{modelConnected ? "Model ready" : status?.model?.status === "offline" ? "Model offline" : status?.model?.status === "loading" ? "Model loading" : "Checking model"}</span>
          </button>
          <SchemeToggle />
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
            <button className="btn btn--ghost import-button" onClick={() => importInput.current?.click()}><Import size={15} /><span>Import</span></button>
            <input ref={importInput} className="sr-only" type="file" accept=".json,.boundless.json,application/json" onChange={(event) => void onImport(event.target.files?.[0])} />
            <button className="btn btn--ghost archive-toggle" aria-pressed={showArchived} onClick={() => setShowArchived(!showArchived)}><Archive size={15} />{showArchived ? "Hide archive" : "Show archive"}</button>
            <button className="btn btn--secondary" onClick={() => { create.reset(); setCreateOpen(true); }}><Plus size={15} />New world</button>
          </div>
        </div>
        {notice && <p className="notice-line" role="status"><Check size={14} />{notice}</p>}
        {importError && <p className="notice-line notice-line--error" role="alert"><CircleAlert size={14} />{importError}</p>}
        {worlds.isLoading && <div className="loading-line"><LoaderCircle className="spin" size={16} />Opening your archive…</div>}
        {worlds.isError && <div className="service-note" role="alert"><WifiOff size={18} /><div><strong>Campaign storage is unreachable.</strong><p>Start PostgreSQL with <code>docker compose up -d --wait</code>, then refresh this page.</p></div></div>}
        {worlds.data?.length === 0 && <div className="empty-worlds"><BookOpenText size={19} /><p>{showArchived ? "No archived worlds." : "No worlds yet. The first sentence is yours."}</p><button className="btn btn--primary" onClick={() => setCreateOpen(true)}>Begin a world <ArrowUpRight size={14} /></button></div>}
        {!!worlds.data?.length && (() => {
          const query = worldSearch.trim().toLocaleLowerCase();
          const shown = worlds.data.filter((campaign) => !query || `${worldTitle(campaign)} ${campaign.premise} ${campaign.protagonist_name} ${campaign.genre} ${campaign.current_location}`
            .toLocaleLowerCase().includes(query));
          const latest = !showArchived && !query ? [...worlds.data].filter((campaign) => !campaign.archived && campaign.turn_count > 0)
            .sort((left, right) => new Date(right.last_played).getTime() - new Date(left.last_played).getTime())[0] : undefined;
          const rest = shown.filter((campaign) => campaign.id !== latest?.id);
          const href = (campaign: CampaignCard) => `/campaign/${campaign.id}?branch=${campaign.branch_id ?? ""}`;
          return <>
            {latest && <motion.article className={`world-feature theme-${latest.theme?.family ?? "neutral"}`}
              initial={{ opacity: 0, y: reduceMotion ? 0 : 10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: reduceMotion ? 0 : 0.35, ease: "easeOut" }}>
              <div className="world-feature-art" aria-hidden="true" />
              <div className="world-feature-body">
                <p className="world-kicker"><span className="mood-mark" aria-hidden="true" />Last played {timeAgo(latest.last_played).toLocaleLowerCase()} · turn {latest.turn_count}</p>
                <h3>{worldTitle(latest)}</h3>
                <p className="world-excerpt">{excerpt(latest.premise)}</p>
                <p className="world-byline">{[heroName(latest), MOOD_NAME[latest.theme?.family ?? ""] ?? latest.genre, latest.current_location].filter(Boolean).join(" · ")}</p>
                <div className="world-feature-actions">
                  <Link className="btn btn--primary world-feature-go" href={href(latest)}>Return to the story<ArrowUpRight size={16} /></Link>
                  <WorldMenu campaign={latest} onRename={onRename} onArchive={onArchive} onDuplicate={onDuplicate} onDelete={onDelete} onError={setNotice} />
                </div>
              </div>
            </motion.article>}
            {(rest.length > 0 || query) && <div className="ledger-head">
              <span>{showArchived ? "Archive" : latest ? "Other worlds" : "All worlds"}<em>{rest.length}</em></span>
              {worlds.data.length > 6 && <label className="world-search"><Search size={14} aria-hidden="true" />
                <input type="search" value={worldSearch} onChange={(event) => setWorldSearch(event.target.value)} placeholder="Search" aria-label="Search worlds" /></label>}
            </div>}
            <ol className="world-ledger">
              {rest.map((campaign, index) => (
                <motion.li className={`ledger-row theme-${campaign.theme?.family ?? "neutral"}${campaign.archived ? " is-archived" : ""}`} key={campaign.id}
                  initial={{ opacity: 0, y: reduceMotion ? 0 : 6 }} animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: reduceMotion ? 0 : 0.25, delay: reduceMotion ? 0 : Math.min(index * 0.03, 0.3), ease: "easeOut" }}>
                  <span className="ledger-index">{String(index + 1).padStart(2, "0")}</span>
                  <div className="ledger-main">
                    <h3><Link href={href(campaign)}>{worldTitle(campaign)}</Link>{campaign.archived && <small>Archived</small>}</h3>
                    <p className="world-excerpt">{excerpt(campaign.premise)}</p>
                    <p className="world-byline"><span className="mood-mark" aria-hidden="true" />{[heroName(campaign), MOOD_NAME[campaign.theme?.family ?? ""] ?? campaign.genre, campaign.current_location].filter(Boolean).join(" · ")}</p>
                  </div>
                  <div className="ledger-meta"><strong>{campaign.turn_count}</strong><span>{campaign.turn_count === 1 ? "turn" : "turns"}</span><small>{timeAgo(campaign.last_played)}</small></div>
                  <div className="ledger-actions">
                    <WorldMenu campaign={campaign} onRename={onRename} onArchive={onArchive} onDuplicate={onDuplicate} onDelete={onDelete} onError={setNotice} />
                    <ArrowUpRight size={18} className="ledger-go" aria-hidden="true" />
                  </div>
                </motion.li>
              ))}
            </ol>
            {!shown.length && <p className="empty-card">No worlds match “{worldSearch.trim()}”.</p>}
          </>;
        })()}
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
