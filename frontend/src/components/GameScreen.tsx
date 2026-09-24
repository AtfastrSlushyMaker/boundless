"use client";

import Link from "next/link";
import Image from "next/image";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ArrowLeft, ArrowUpRight, Backpack, CheckCircle2, ChevronDown, CircleAlert, Clock3, Flag, GitBranch,
  Map, Menu, MessageSquareText, Moon, PanelLeftClose, PanelLeftOpen, Pencil, RotateCcw,
  ScrollText, Send, Settings2, Shield, Sparkles, StopCircle, Sun, Sunrise, Sunset, Users, X,
} from "lucide-react";
import { FormEvent, KeyboardEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AtlasArtwork } from "@/components/AtlasArtwork";
import { ModelSettingsDialog } from "@/components/ModelSettingsDialog";
import { CampaignHealth } from "@/components/CampaignHealth";
import { GenerationProgress } from "@/components/GenerationProgress";
import { faceCrop, PortraitButton } from "@/components/PortraitLightbox";
import { ProfileList } from "@/components/LivingProfile";
import { PeoplePanel } from "@/components/PeoplePanel";
import { ChangeToasts, TurnChanges } from "@/components/TurnChanges";
import { api, CampaignDetail, GameMode, Objective, PlayerProfile, SceneMood, StreamEvent, ThemeFamily, TurnChange, portraitUrl, streamTurn } from "@/lib/api";

const OPENING_ACTION = "Open on the campaign's stated starting moment.";
type LorePanel = "character" | "people" | "quests" | "inventory" | "world" | "rules" | "journal";
type WorldClock = { day?: number; time_of_day?: string; label?: string };

function worldClock(campaign: CampaignDetail): WorldClock {
  const clock = campaign.current_state.world_clock as WorldClock | undefined;
  return clock ?? { label: String(campaign.current_state.world_time ?? "") };
}

function ClockIcon({ period, size = 13 }: { period?: string; size?: number }) {
  if (period === "dawn" || period === "before dawn") return <Sunrise size={size} />;
  if (period === "evening") return <Sunset size={size} />;
  if (period === "morning" || period === "midday" || period === "afternoon") return <Sun size={size} />;
  if (period) return <Moon size={size} />;
  return <Clock3 size={size} />;
}

const MOOD_LABEL: Record<string, string> = {
  calm: "Calm", tense: "Tense", danger: "Danger", combat: "Combat", mystery: "Mystery", grief: "Grief",
  romance: "Tender", triumph: "Triumph", eerie: "Eerie", wonder: "Wonder",
};

function sceneMood(campaign: CampaignDetail): SceneMood {
  const mood = campaign.current_state.scene_mood as SceneMood | undefined;
  return mood && typeof mood.mood === "string" ? mood : { mood: "calm", intensity: 0.2 };
}

function useAdaptiveMood(): [boolean, (value: boolean) => void] {
  const [adaptive, setAdaptive] = useState(true);
  useEffect(() => {
    try {
      const stored = window.localStorage.getItem("boundless:adaptive-mood");
      if (stored === "off") { const timer = window.setTimeout(() => setAdaptive(false), 0); return () => window.clearTimeout(timer); }
    } catch { /* storage unavailable: keep the default */ }
  }, []);
  const update = (value: boolean) => {
    setAdaptive(value);
    try { window.localStorage.setItem("boundless:adaptive-mood", value ? "on" : "off"); } catch { /* ignore */ }
  };
  return [adaptive, update];
}

/** A slow, low-contrast light layer that follows the scene's mood and time of day. */
function Ambience({ mood, period, intensity }: { mood: string; period?: string; intensity: number }) {
  const reduceMotion = useReducedMotion();
  return <AnimatePresence initial={false}>
    <motion.div key={`${mood}-${period ?? ""}`} className="ambience" data-mood={mood} data-period={period || "none"} aria-hidden="true"
      style={{ ["--mood-intensity" as string]: String(Math.max(0.25, Math.min(1, intensity))) }}
      initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: reduceMotion ? 0 : 1.6, ease: "easeInOut" }}>
      <span className="ambience-glow" /><span className="ambience-haze" /><span className="ambience-vignette" />
    </motion.div>
  </AnimatePresence>;
}

function QuestCard({ objective, index }: { objective: Objective; index: number }) {
  const reduceMotion = useReducedMotion();
  const done = objective.status === "completed";
  return <motion.li layout={!reduceMotion} className={`quest-card quest-card--${objective.status}`}
    initial={reduceMotion ? false : { opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
    transition={{ duration: 0.25, delay: reduceMotion ? 0 : index * 0.04 }}>
    <span className="quest-mark" aria-hidden="true">{done ? <CheckCircle2 size={15} /> : <Flag size={14} />}</span>
    <div>
      <strong>{objective.title}</strong>
      {objective.description && objective.description !== objective.title && <p>{objective.description}</p>}
      {!!objective.aliases?.length && <p className="quest-aliases">Also: {objective.aliases.join(" · ")}</p>}
      {objective.resolution_note && <p className="quest-note">{objective.resolution_note}</p>}
    </div>
  </motion.li>;
}
type RetryRequest = { action: string; instruction?: string; targetTurnId?: string };

function Markdown({ content }: { content: string }) {
  return <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>;
}

function MemoryActionMenu({ campaign, turnId, branchId, content, onChanged, onError }: {
  campaign: CampaignDetail; turnId: string; branchId: string; content: string;
  onChanged: (detail: CampaignDetail) => void; onError: (message: string) => void;
}) {
  const reduceMotion = useReducedMotion();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(content);
  const [busy, setBusy] = useState(false);
  const [promptDialog, setPromptDialog] = useState<{ kind: "rewrite" | "branch"; value: string } | null>(null);
  useEffect(() => {
    if (!promptDialog || busy) return;
    const onKey = (event: globalThis.KeyboardEvent) => { if (event.key === "Escape") setPromptDialog(null); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [promptDialog, busy]);
  const save = async () => {
    setBusy(true); onError("");
    try { onChanged(await api.editTurn(turnId, draft)); setEditing(false); }
    catch (error) { onError(error instanceof Error ? error.message : "The edit could not be saved."); }
    finally { setBusy(false); }
  };
  const regenerate = () => setPromptDialog({ kind: "rewrite", value: "" });
  const rewind = () => {
    if (!window.confirm("Return this timeline to the end of this turn? Later turns remain in the archive but leave the active story.")) return;
    window.dispatchEvent(new CustomEvent("boundless:rewind", { detail: { turnId, branchId } }));
  };
  const fork = () => setPromptDialog({ kind: "branch", value: "What if…" });
  const submitPrompt = async (event: FormEvent) => {
    event.preventDefault();
    if (!promptDialog) return;
    const value = promptDialog.value.trim();
    if (promptDialog.kind === "rewrite") {
      window.dispatchEvent(new CustomEvent("boundless:regenerate", { detail: { turnId, instruction: value, branchId } }));
      setPromptDialog(null);
      return;
    }
    if (!value) return;
    setBusy(true);
    try {
      const branch = await api.createBranch(campaign.id, value, branchId, turnId);
      await api.activateBranch(campaign.id, branch.id);
      window.dispatchEvent(new CustomEvent("boundless:branch", { detail: { branchId: branch.id } }));
      setPromptDialog(null);
    } catch (error) { onError(error instanceof Error ? error.message : "The branch could not be created."); }
    finally { setBusy(false); }
  };

  if (editing) return <div className="edit-narration">
    <label htmlFor={`edit-${turnId}`}>Revise the Game Master&apos;s narration</label>
    <textarea id={`edit-${turnId}`} value={draft} onChange={(event) => setDraft(event.target.value)} rows={Math.min(14, Math.max(5, draft.split("\n").length + 2))} />
    <div className="edit-actions"><button className="quiet-button" onClick={() => { setDraft(content); setEditing(false); }}>Cancel</button><button className="primary-button" onClick={() => void save()} disabled={busy}>{busy ? "Rebuilding state…" : "Save version"}</button></div>
  </div>;

  return <>
    <div className="turn-tools" aria-label="Narration actions">
      <button className="tool-button" aria-label="Edit narration" title="Edit narration" disabled={busy} onClick={() => { setDraft(content); setEditing(true); }}><Pencil size={15} /><span>Edit</span></button>
      <button className="tool-button" aria-label="Regenerate narration" title="Regenerate" disabled={busy} onClick={regenerate}><RotateCcw size={15} /><span>Rewrite</span></button>
      <button className="tool-button" aria-label="Branch from this turn" title="Branch from here" disabled={busy} onClick={fork}><GitBranch size={15} /><span>Branch</span></button>
      <button className="tool-button" aria-label="Rewind to this turn" title="Rewind to this turn" disabled={busy} onClick={rewind}><ArrowLeft size={15} /><span>Rewind</span></button>
    </div>
    <AnimatePresence initial={false}>
      {promptDialog && <motion.div key={`${promptDialog.kind}-${turnId}`} className="dialog-scrim" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) setPromptDialog(null); }}
        initial={false} exit={{ y: reduceMotion ? 0 : 6 }} transition={{ duration: reduceMotion ? 0 : 0.16, ease: "easeOut" }}>
        <motion.section className="prompt-dialog" role="dialog" aria-modal="true" aria-labelledby={`turn-prompt-title-${turnId}`}
          initial={{ y: reduceMotion ? 0 : 12 }} animate={{ y: 0 }} exit={{ y: reduceMotion ? 0 : 8 }}
          transition={{ duration: reduceMotion ? 0 : 0.2, ease: "easeOut" }}>
          <header className="dialog-head">
            <div><p className="section-overline">{promptDialog.kind === "rewrite" ? "REVISE THIS MOMENT" : "A DIFFERENT TIMELINE"}</p>
              <h2 id={`turn-prompt-title-${turnId}`}>{promptDialog.kind === "rewrite" ? "Rewrite this passage" : "Name the branch"}</h2></div>
            <button className="icon-button" aria-label="Close" onClick={() => setPromptDialog(null)} disabled={busy}><X size={18} /></button>
          </header>
          <p className="create-lead">{promptDialog.kind === "rewrite" ? "Add an optional direction for the next version. Leave it blank for a fresh take." : "This timeline starts from the selected turn. Give the possibility a name."}</p>
          <form onSubmit={(event) => void submitPrompt(event)}>
            <label className="prompt-field-label" htmlFor={`turn-prompt-${turnId}`}>{promptDialog.kind === "rewrite" ? "Direction for the rewrite" : "Timeline name"}</label>
            {promptDialog.kind === "rewrite" ? <textarea id={`turn-prompt-${turnId}`} autoFocus rows={4} maxLength={2_000} value={promptDialog.value} onChange={(event) => setPromptDialog({ kind: "rewrite", value: event.target.value })} placeholder="Keep the same scene, but let the envoy reveal why she came…" disabled={busy} /> : <input id={`turn-prompt-${turnId}`} autoFocus maxLength={120} value={promptDialog.value} onChange={(event) => setPromptDialog({ kind: "branch", value: event.target.value })} disabled={busy} />}
            <footer className="dialog-actions">
              <button type="button" className="quiet-button" onClick={() => setPromptDialog(null)} disabled={busy}>Cancel</button>
              <button type="submit" className="primary-button" disabled={busy || (promptDialog.kind === "branch" && !promptDialog.value.trim())}>
                {promptDialog.kind === "rewrite" ? <RotateCcw size={15} /> : <GitBranch size={15} />}
                <span>{busy ? "Creating branch" : promptDialog.kind === "rewrite" ? "Rewrite turn" : "Create branch"}</span>
              </button>
            </footer>
          </form>
        </motion.section>
      </motion.div>}
    </AnimatePresence>
  </>;
}

function profileOf(campaign: CampaignDetail): PlayerProfile | null {
  const profile = campaign.current_state.player_profile as PlayerProfile | undefined;
  return profile && Array.isArray(profile.traits) ? profile : null;
}

function SidebarContent({ panel, campaign, onRefreshSetup, refreshingSetup, refreshedSetup,
                          onReindexPeople, reindexingPeople, reindexedPeople, onRefresh,
                          onRefreshStory, refreshingStory, storyNotice }: {
  panel: LorePanel; campaign: CampaignDetail; onRefreshSetup: () => void; refreshingSetup: boolean; refreshedSetup: boolean;
  onRefreshStory: () => void; refreshingStory: boolean; storyNotice: string;
  onReindexPeople: () => void; reindexingPeople: boolean; reindexedPeople: boolean;
  onRefresh: () => void;
}) {
  const [characterTab, setCharacterTab] = useState<"overview" | "traits" | "history" | "goals" | "reputation">("overview");
  const [inventorySearch, setInventorySearch] = useState("");
  const [journalTab, setJournalTab] = useState<"events" | "memories" | "secrets" | "health">("events");
  if (panel === "character") {
    const constitution = campaign.constitution;
    const protagonist = campaign.characters.find((person) => person.name === campaign.protagonist_name);
    const catalogue = (protagonist?.abilities ?? []).filter((ability) => ability.status === "ACTIVE");
    const abilities = catalogue.length ? [] : [...((constitution.abilities as string[] | undefined) ?? []), ...((constitution.powers as string[] | undefined) ?? [])].filter((value, index, all) => all.indexOf(value) === index);
    const condition = (campaign.current_state.player_condition ?? {}) as { physical_status?: string; injuries?: string[]; conditions?: string[] };
    const identity = (protagonist?.attributes ?? ((constitution.starting_state as Record<string, unknown> | undefined)?.identity as Record<string, unknown> | undefined) ?? {});
    const money = (campaign.current_state.money ?? protagonist?.attributes?.money) as { amount?: number; currency?: string } | undefined;
    const identityDetails = ([ ["Sex", identity.sex], ["Gender", identity.gender], ["Pronouns", identity.pronouns] ] as const)
      .filter(([, value]) => typeof value === "string").map(([label, value]) => `${label}: ${value}`);
    const portrait = portraitUrl(protagonist?.attributes?.avatar_url);
    const profile = profileOf(campaign);
    const fallback = (values: unknown) => ((values as string[] | undefined) ?? []).map((text, index) => ({ id: `${index}-${text}`, text, source: "premise", turn_index: 0, status: "active" }));
    const lists = {
      traits: profile?.traits ?? fallback(constitution.traits), history: profile?.history ?? fallback(constitution.history),
      goals: profile?.goals ?? fallback(constitution.preferences), reputation: profile?.reputation ?? [],
    };
    return <div className="lore-content character-sheet">
      <p className="lore-label">YOUR CHARACTER</p>
      <div className="character-sheet-head">{portrait ? <PortraitButton className={`character-sheet-portrait${faceCrop(protagonist?.attributes).className}`} style={faceCrop(protagonist?.attributes).style} src={portrait} name={campaign.protagonist_name} caption="Player character"><Image src={portrait} alt="" width={72} height={72} unoptimized /></PortraitButton>
        : <div className="character-sheet-portrait"><span>{campaign.protagonist_name.charAt(0)}</span></div>}<div><h2 className="lore-name">{campaign.protagonist_name}</h2><p>{protagonist?.role || "Player character"}</p></div></div>
      <nav className="sheet-tabs" aria-label="Character details">{(["overview", "traits", "history", "goals", "reputation"] as const).map((tab) => <button type="button" key={tab} aria-current={characterTab === tab ? "page" : undefined} onClick={() => setCharacterTab(tab)}>{tab[0].toUpperCase() + tab.slice(1)}</button>)}</nav>
      {characterTab === "overview" && <>
        <dl className="character-facts">{identityDetails.map((entry) => { const [label, value] = entry.split(": "); return <div key={label}><dt>{label}</dt><dd>{value}</dd></div>; })}
          {money && typeof money.amount === "number" && <div><dt>Money</dt><dd>{money.amount.toLocaleString()} {money.currency ?? ""}</dd></div>}
          <div><dt>Status</dt><dd>{String(condition.physical_status ?? campaign.current_state.player_status ?? "alive")}</dd></div></dl>
        {!!(condition.injuries?.length || condition.conditions?.length) && <div className="condition-chips" aria-label="Injuries and conditions">
          {condition.injuries?.map((entry) => <span key={entry} className="condition-chip condition-chip--injury">{entry}</span>)}
          {condition.conditions?.map((entry) => <span key={entry} className="condition-chip">{entry}</span>)}</div>}
        {!!catalogue.length && <section className="lore-group"><h3>Ability catalogue</h3><ul className="ability-cards">{catalogue.map((ability, index) =>
          <motion.li key={ability.id} initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: index * 0.05 }}>
            <div className="ability-card-head"><Sparkles size={13} /><strong>{ability.name}</strong>
              <span>{ability.acquired_turn_index ? `Turn ${ability.acquired_turn_index}` : ability.source || "Innate"}</span></div>
            {ability.description && <p>{ability.description}</p>}
            {ability.source && ability.acquired_turn_index ? <p className="ability-source">{ability.source}</p> : null}
            {!!ability.limitations?.length && <p className="ability-limits">Limits: {ability.limitations.join("; ")}</p>}
          </motion.li>)}</ul></section>}
        {protagonist?.personality && <section className="lore-group"><h3>Personality</h3><p className="lore-copy">{protagonist.personality}</p></section>}
        {!!abilities.length && <section className="lore-group"><h3>Established abilities</h3><ul className="plain-list">{abilities.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul></section>}
        {!!(constitution.limitations as string[] | undefined)?.length && <section className="lore-group"><h3>Limitations</h3><ul className="plain-list">{(constitution.limitations as string[]).map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul></section>}
      </>}
      {characterTab !== "overview" && <section className="lore-group"><h3>{characterTab[0].toUpperCase() + characterTab.slice(1)}</h3>
        <ProfileList entries={lists[characterTab]} empty={characterTab === "reputation" ? "The world has not formed an opinion yet." : "Nothing recorded yet."} /></section>}
      <div className="profile-actions">
        <button type="button" className="health-button" onClick={onRefreshStory} disabled={refreshingStory}>
          <Sparkles size={14} className={refreshingStory ? "spin" : undefined} />{refreshingStory ? "Reading the story…" : "Refresh from story"}</button>
        <button type="button" className="text-button" onClick={onRefreshSetup} disabled={refreshingSetup}>{refreshingSetup ? "Reading premise…" : refreshedSetup ? "Premise re-read" : "Re-read premise"}</button>
      </div>
      {storyNotice && <p className="profile-notice" role="status">{storyNotice}</p>}
      {profile?.updated_through_turn ? <p className="summary-meta">Profile follows the story through turn {profile.updated_through_turn}.</p> : null}
    </div>;
  }
  if (panel === "people") return <PeoplePanel campaign={campaign} onReindex={onReindexPeople}
    rebuilding={reindexingPeople} rebuilt={reindexedPeople} onRefresh={onRefresh} />;
  if (panel === "quests") {
    const active = campaign.objectives.filter((objective) => objective.status === "active");
    const resolved = campaign.objectives.filter((objective) => ["completed", "failed", "abandoned"].includes(objective.status));
    return <div className="lore-content quests-sheet"><p className="lore-label">WHAT YOU ARE AFTER</p><h2 className="lore-name">Objectives</h2>
      {!active.length && !resolved.length && <p className="lore-copy">Objectives appear as the story gives you reasons to act.</p>}
      {!!active.length && <section className="lore-group"><h3>Open</h3><ul className="quest-list">{active.map((objective, index) => <QuestCard key={objective.id} objective={objective} index={index} />)}</ul></section>}
      {!!resolved.length && <section className="lore-group"><h3>Resolved</h3><ul className="quest-list">{resolved.map((objective, index) => <QuestCard key={objective.id} objective={objective} index={index} />)}</ul></section>}
    </div>;
  }
  if (panel === "inventory") {
    const items = campaign.inventory.filter((item) => `${item.name} ${item.significance} ${item.condition}`.toLowerCase().includes(inventorySearch.toLowerCase()));
    return <div className="lore-content inventory-sheet"><p className="lore-label">ON YOUR PERSON</p><h2 className="lore-name">Inventory</h2>
      <p className="lore-copy">{campaign.inventory.length} recorded {campaign.inventory.length === 1 ? "item" : "items"}</p>
      <input type="search" aria-label="Search inventory" placeholder="Search your belongings" value={inventorySearch} onChange={(event) => setInventorySearch(event.target.value)} />
      {items.length === 0 && <p className="lore-copy">{campaign.inventory.length ? "No items match that search." : "Nothing has been recorded in your inventory."}</p>}
      <ul className="inventory-tiles">{items.map((item) => <li key={item.id}><span className="inventory-object" aria-hidden="true">{item.name.charAt(0).toUpperCase()}</span><strong>{item.name}</strong><span>{item.quantity > 1 ? `${item.quantity} held · ` : ""}{item.condition || "Condition unknown"}</span>{item.significance && <p>{item.significance}</p>}</li>)}</ul>
    </div>;
  }
  if (panel === "world") return <div className="lore-content world-sheet"><p className="lore-label">THE KNOWN WORLD</p><h2 className="lore-name">Places</h2>
    <div className="world-sheet-map"><AtlasArtwork compact /><span>{campaign.current_location || "Where the story stands"}</span></div>
    <p className="lore-copy">{campaign.locations.length} known {campaign.locations.length === 1 ? "place" : "places"}. The map is atmospheric; only recorded locations are listed below.</p>
    <ul className="world-place-list">{campaign.locations.map((place) => <li key={place.id}><strong>{place.name}</strong>{place.region && <span>{place.region}</span>}{place.description && <p>{place.description}</p>}</li>)}</ul>
    {!campaign.locations.length && <p className="lore-copy">Places will appear as you discover them.</p>}
  </div>;
  if (panel === "rules") {
    const learned = profileOf(campaign)?.world_rules ?? [];
    const setupRules = ((campaign.constitution.rules as Array<{ statement: string; type: string; strength: string }> | undefined) ?? [])
      .filter((rule) => rule.type !== "GOAL");
    return <div className="lore-content"><p className="lore-label">WORLD CONSTITUTION</p><h2 className="lore-name">Rules that hold</h2>
    <p className="lore-copy">Hard rules come from your premise and explicit canon commands. The world also teaches you how it works as you play.</p>
    <ul className="rule-list">{campaign.canon_rules.map((rule, index) => <li key={rule.id ?? index}><Shield size={15} /><span>{rule.statement}</span></li>)}
      {setupRules.map((rule) => <li key={`${rule.type}-${rule.statement}`} className={`rule-${rule.strength.toLowerCase()}`}><Shield size={15} /><span>{rule.statement}<em>{rule.strength === "HARD" ? "Hard" : "Soft"} · {rule.type.replaceAll("_", " ").toLowerCase()}</em></span></li>)}</ul>
    <section className="lore-group"><h3>Learned in the story</h3><ProfileList entries={learned} empty="Rules you discover in play will appear here." /></section>
    <details className="prompt-record"><summary>Original premise</summary><p>{campaign.original_prompt}</p></details>
  </div>;
  }
  return <div className="lore-content journal-sheet"><p className="lore-label">CAMPAIGN RECORD</p><h2 className="lore-name">Journal</h2>
    {campaign.summary && <section className="summary-note"><h3>What has happened</h3><p>{campaign.summary}</p>
      {campaign.summary_state && <p className="summary-meta">Through turn {campaign.summary_state.through_turn_index}{campaign.summary_state.method === "deterministic" ? " · compiled from the record" : ""}{campaign.summary_state.last_error ? " · model retry pending" : ""}</p>}</section>}
    <nav className="sheet-tabs" aria-label="Journal entries">{(["events", "memories", "secrets", "health"] as const).map((tab) => <button type="button" key={tab} aria-current={journalTab === tab ? "page" : undefined} onClick={() => setJournalTab(tab)}>{tab[0].toUpperCase() + tab.slice(1)}</button>)}</nav>
    {campaign.events.length === 0 && campaign.memories.length === 0 && !campaign.summary && <p className="lore-copy">Important events will gather here as the story unfolds.</p>}
    {journalTab === "events" && <ul className="journal-list">{campaign.events.map((event, index) => <li key={event.id ?? index}><span>{event.certainty ?? "RECORDED"}</span><p>{event.content}</p></li>)}</ul>}
    {journalTab === "memories" && <ul className="journal-list">{campaign.memories.map((memory, index) => <li key={String(memory.id ?? index)}><span>{String(memory.memory_type ?? "MEMORY")}</span><p>{String(memory.content ?? "")}</p></li>)}</ul>}
    {journalTab === "health" && <CampaignHealth campaign={campaign} onRepaired={() => onRefresh()} />}
    {journalTab === "secrets" && <ul className="journal-list">{campaign.known_secrets.map((secret) => <li key={secret.id}><span>{secret.name}</span><p>{secret.content}</p></li>)}</ul>}
  </div>;
}

export function GameScreen({ campaignId, requestedBranchId }: { campaignId: string; requestedBranchId?: string }) {
  const router = useRouter();
  const cache = useQueryClient();
  const [panel, setPanel] = useState<LorePanel>("character");
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [action, setAction] = useState("");
  const [liveText, setLiveText] = useState("");
  const [generating, setGenerating] = useState(false);
  const [awaitingStoryRefresh, setAwaitingStoryRefresh] = useState(false);
  const [streamError, setStreamError] = useState("");
  const [retryRequest, setRetryRequest] = useState<RetryRequest | null>(null);
  const [isFollowing, setIsFollowing] = useState(true);
  const [showJump, setShowJump] = useState(false);
  const [notice, setNotice] = useState("");
  const [stage, setStage] = useState<"" | "writing" | "interpreting">("");
  const [latestChanges, setLatestChanges] = useState<{ key: string; changes: TurnChange[] } | null>(null);
  const [freshTurnId, setFreshTurnId] = useState<string | null>(null);
  const [adaptiveMood, setAdaptiveMood] = useAdaptiveMood();
  const abortRef = useRef<AbortController | null>(null);
  const readerRef = useRef<HTMLDivElement>(null);
  const startedOpening = useRef(false);
  const health = useQuery({ queryKey: ["health"], queryFn: api.health, refetchInterval: 18_000 });
  const campaignQuery = useQuery({
    queryKey: ["campaign", campaignId, requestedBranchId],
    queryFn: () => api.campaign(campaignId, requestedBranchId),
  });
  const campaign = campaignQuery.data;
  const branchId = campaign?.branch.id;
  const latestTurn = campaign?.turns.at(-1);
  const latestChoices = latestTurn?.suggested_actions ?? [];
  const modelReady = health.data?.model?.status === "connected";

  const changeGameMode = useMutation({
    mutationFn: (gameMode: GameMode) => {
      if (!branchId) throw new Error("Open a campaign before changing its play style.");
      return api.setGameMode(campaignId, branchId, gameMode);
    },
    onSuccess: (detail) => {
      cache.setQueryData(["campaign", campaignId, requestedBranchId], detail);
      void cache.invalidateQueries({ queryKey: ["campaigns"] });
      setNotice("");
    },
    onError: (error) => setNotice(error.message),
  });
  const changeTheme = useMutation({
    mutationFn: (family: ThemeFamily) => {
      if (!branchId) throw new Error("Open a campaign before changing its mood.");
      return api.setTheme(campaignId, branchId, family);
    },
    onSuccess: (detail) => {
      cache.setQueryData(["campaign", campaignId, requestedBranchId], detail);
      void cache.invalidateQueries({ queryKey: ["campaigns"] });
      setNotice("");
    },
    onError: (error) => setNotice(error.message),
  });
  const refreshSetup = useMutation({
    mutationFn: () => {
      if (!branchId) throw new Error("Open a campaign before refreshing its details.");
      return api.refreshSetup(campaignId, branchId);
    },
    onSuccess: (detail) => {
      cache.setQueryData(["campaign", campaignId, requestedBranchId], detail);
      void cache.invalidateQueries({ queryKey: ["campaigns"] });
      setNotice("");
    },
    onError: (error) => setNotice(error.message),
  });
  const [storyNotice, setStoryNotice] = useState("");
  const refreshStory = useMutation({
    mutationFn: () => {
      if (!branchId) throw new Error("Open a campaign before refreshing it.");
      return api.refreshStory(campaignId, branchId);
    },
    onSuccess: (result) => {
      cache.setQueryData(["campaign", campaignId, requestedBranchId], result.campaign);
      setStoryNotice(result.changes.length ? `${result.changes.length} changes from the story.` : "Everything is already up to date.");
      if (result.changes.length) setLatestChanges({ key: `profile-${Date.now()}`, changes: result.changes.map((change) => ({ ...change, type: "character" })) });
    },
    onError: (error) => setStoryNotice(error.message),
  });
  const reindexPeople = useMutation({
    mutationFn: () => {
      if (!branchId) throw new Error("Open a campaign before recovering people.");
      return api.reindexPeople(campaignId, branchId);
    },
    onSuccess: (detail) => {
      cache.setQueryData(["campaign", campaignId, requestedBranchId], detail);
      setNotice("");
    },
    onError: (error) => setNotice(error.message),
  });

  const refetchCampaign = useCallback(async () => {
    await cache.invalidateQueries({ queryKey: ["campaign", campaignId] });
    await cache.invalidateQueries({ queryKey: ["campaigns"] });
  }, [cache, campaignId]);

  const runStream = useCallback(async (storyAction: string, instruction?: string, targetTurnId?: string) => {
    if (!branchId || generating || changeGameMode.isPending || changeTheme.isPending) return;
    const currentRequest = { action: storyAction, instruction, targetTurnId };
    const controller = new AbortController();
    abortRef.current = controller;
    setGenerating(true); setStreamError(""); setNotice(""); setLiveText(""); setRetryRequest(null);
    try {
      const onEvent = (event: StreamEvent) => {
        if (event.type === "delta") { setStage("writing"); setLiveText((current) => current + event.text); }
        if (event.type === "status" && event.stage === "interpreting") setStage("interpreting");
        if (event.type === "replace") setLiveText(event.text);
        if (event.type === "error") { setStreamError(event.message); setRetryRequest(currentRequest); setGenerating(false); setLiveText(""); setStage(""); }
        if (event.type === "complete") {
          setFreshTurnId(event.turn_id);
          setLatestChanges({ key: event.turn_id, changes: event.changes ?? [] });
          setGenerating(false); setLiveText(""); setStage(""); setRetryRequest(null); setAwaitingStoryRefresh(true);
          void refetchCampaign().finally(() => setAwaitingStoryRefresh(false));
        }
      };
      if (targetTurnId) {
        const response = await fetch(`${process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000"}/api/turns/${targetTurnId}/regenerate`, {
          method: "POST", headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
          body: JSON.stringify({ action: "regenerate", instruction }), signal: controller.signal,
        });
        if (!response.ok || !response.body) throw new Error("Could not regenerate this passage.");
        const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = "";
        while (true) {
          const part = await reader.read(); buffer += decoder.decode(part.value, { stream: !part.done });
          const frames = buffer.split(/\r?\n\r?\n/); buffer = frames.pop() ?? "";
          for (const frame of frames) {
            const data = frame.split(/\r?\n/).find((line) => line.startsWith("data:"))?.slice(5).trim();
            if (data && data !== "[DONE]") { try { onEvent(JSON.parse(data) as StreamEvent); } catch { /* malformed frame */ } }
          }
          if (part.done) break;
        }
      } else {
        await streamTurn(campaignId, { action: storyAction, branch_id: branchId, instruction }, onEvent, controller.signal);
      }
    } catch (error) {
      if (!(error instanceof DOMException && error.name === "AbortError")) {
        setStreamError(error instanceof Error ? error.message : "Generation failed.");
        setRetryRequest(currentRequest);
      }
    } finally {
      setGenerating(false); setStage(""); abortRef.current = null;
    }
  }, [branchId, campaignId, generating, changeGameMode.isPending, changeTheme.isPending, refetchCampaign]);

  const sendAction = async (event: FormEvent) => {
    event.preventDefault();
    const trimmed = action.trim();
    if (!trimmed || generating || changeGameMode.isPending || changeTheme.isPending) return;
    setAction("");
    await runStream(trimmed);
  };

  useEffect(() => {
    if (!campaign || campaign.turns.length || startedOpening.current || !modelReady || !branchId) return;
    startedOpening.current = true;
    void runStream(OPENING_ACTION);
  }, [campaign, branchId, modelReady, runStream]);

  useEffect(() => {
    const el = readerRef.current;
    if (!el || !isFollowing) return;
    el.scrollTop = el.scrollHeight;
    setShowJump(false);
  }, [campaign?.turns, liveText, isFollowing]);

  useEffect(() => {
    const regenerate = (event: Event) => {
      const detail = (event as CustomEvent<{ turnId: string; instruction: string; branchId: string }>).detail;
      if (detail.branchId === branchId) void runStream("", detail.instruction, detail.turnId);
    };
    const rewind = async (event: Event) => {
      const detail = (event as CustomEvent<{ turnId: string; branchId: string }>).detail;
      if (detail.branchId !== branchId) return;
      try {
        await api.rewind(campaignId, branchId!, detail.turnId);
        await refetchCampaign();
      } catch (error) { setNotice(error instanceof Error ? error.message : "Rewind failed."); }
    };
    const branch = (event: Event) => {
      const detail = (event as CustomEvent<{ branchId: string }>).detail;
      router.replace(`/campaign/${campaignId}?branch=${detail.branchId}`);
    };
    window.addEventListener("boundless:regenerate", regenerate);
    window.addEventListener("boundless:rewind", rewind);
    window.addEventListener("boundless:branch", branch);
    return () => {
      window.removeEventListener("boundless:regenerate", regenerate);
      window.removeEventListener("boundless:rewind", rewind);
      window.removeEventListener("boundless:branch", branch);
    };
  }, [branchId, campaignId, refetchCampaign, router, runStream]);

  const onReaderScroll = () => {
    const el = readerRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 140;
    setIsFollowing(nearBottom);
    setShowJump(!nearBottom);
  };

  const selectBranch = async (id: string) => {
    await api.activateBranch(campaignId, id);
    router.replace(`/campaign/${campaignId}?branch=${id}`);
  };

  const nav = useMemo(() => [
    ["character", "Character", Users], ["people", "People", MessageSquareText], ["quests", "Objectives", Flag],
    ["inventory", "Inventory", Backpack], ["world", "World", Map], ["rules", "World rules", Shield], ["journal", "Journal", ScrollText],
  ] as const, []);

  if (campaignQuery.isLoading) return <main className="game-loading"><span className="brand-glyph">B</span><p>Opening the archive…</p></main>;
  if (campaignQuery.isError || !campaign) return <main className="game-load-error"><Link href="/" className="back-link"><ArrowLeft size={16} />Back to Boundless</Link><CircleAlert size={25} /><h1>This world could not be opened.</h1><p>{campaignQuery.error?.message ?? "Campaign not found."}</p><button className="quiet-button" onClick={() => void campaignQuery.refetch()}>Try again</button></main>;

  return (
    <main className={`game-shell theme-${campaign.theme?.family ?? "neutral"}${adaptiveMood ? " is-adaptive" : ""}`}
      data-mood={adaptiveMood ? sceneMood(campaign).mood : "none"} data-period={worldClock(campaign).time_of_day || "none"}>
      <header className="topbar game-topbar">
        <div className="game-brand-group">
          <button className="icon-button side-toggle desktop-only" onClick={() => setSidebarOpen(!sidebarOpen)} aria-label={sidebarOpen ? "Collapse campaign notes" : "Expand campaign notes"}>{sidebarOpen ? <PanelLeftClose size={18} /> : <PanelLeftOpen size={18} />}</button>
          <button className="icon-button mobile-only" onClick={() => setMobileNavOpen(!mobileNavOpen)} aria-label="Open campaign notes">{mobileNavOpen ? <X size={18} /> : <Menu size={18} />}</button>
          <Link href="/" className="brand-lockup"><span className="brand-glyph" aria-hidden="true">B</span><span>Boundless</span></Link>
        </div>
        <div className="campaign-heading">
          <span className="campaign-title">{campaign.title}</span>
          <span className="campaign-subtitle">{campaign.genre} <span>·</span> {campaign.protagonist_name}</span>
        </div>
        <div className="game-top-actions">
          {adaptiveMood && <motion.button type="button" key={sceneMood(campaign).mood} className="mood-chip" title="The scene's current mood · click to turn adaptive mood off"
            onClick={() => setAdaptiveMood(false)} initial={{ opacity: 0, scale: 0.9 }} animate={{ opacity: 1, scale: 1 }}>
            <span className="mood-dot" aria-hidden="true" /><span>{MOOD_LABEL[sceneMood(campaign).mood] ?? sceneMood(campaign).mood}</span></motion.button>}
          {worldClock(campaign).label && <motion.span key={worldClock(campaign).label} className="world-clock-chip" initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }}>
            <ClockIcon period={worldClock(campaign).time_of_day} /><span>{worldClock(campaign).label}</span></motion.span>}
          <div className="branch-control">
            <GitBranch size={15} />
            <select aria-label="Choose timeline" value={campaign.branch.id} onChange={(event) => void selectBranch(event.target.value)}>
              {campaign.branches.map((branch) => <option key={branch.id} value={branch.id}>{branch.name}</option>)}
            </select><ChevronDown size={14} />
          </div>
          <button className="connection-indicator game-model" onClick={() => setSettingsOpen(true)}>
            <span className={`status-mark ${modelReady ? "status-mark--on" : "status-mark--off"}`} />
            <span>{modelReady ? "Model ready" : health.data?.model?.status === "loading" ? "Model loading" : "Model offline"}</span>
          </button>
          <button className="icon-button" aria-label="Model settings" onClick={() => setSettingsOpen(true)}><Settings2 size={18} /></button>
        </div>
      </header>

      <div className={`game-body ${sidebarOpen ? "sidebar-is-open" : "sidebar-is-closed"}`}>
        <aside className={`campaign-rail ${mobileNavOpen ? "campaign-rail--mobile-open" : ""}`} aria-label="Campaign notes">
          <div className="rail-map">
            <AtlasArtwork compact />
            <div className="rail-map-caption"><Map size={13} /><span>{campaign.current_location || "THE KNOWN WORLD"}</span></div>
          </div>
          <nav className="lore-nav" aria-label="Campaign information">
            {nav.map(([id, label, Icon]) => <button key={id} className={panel === id ? "lore-nav-item lore-nav-item--active" : "lore-nav-item"} onClick={() => { setPanel(id); setMobileNavOpen(false); }} aria-current={panel === id ? "page" : undefined}><Icon size={16} /><span>{label}</span>{panel === id && <motion.span layoutId="lore-cursor" className="lore-cursor" aria-hidden="true" transition={{ type: "spring", stiffness: 380, damping: 34 }} />}</button>)}
          </nav>
          <div className="rail-content"><SidebarContent panel={panel} campaign={campaign} onRefreshSetup={() => refreshSetup.mutate()} refreshingSetup={refreshSetup.isPending} refreshedSetup={refreshSetup.isSuccess} onReindexPeople={() => reindexPeople.mutate()} reindexingPeople={reindexPeople.isPending} reindexedPeople={reindexPeople.isSuccess} onRefresh={refetchCampaign}
            onRefreshStory={() => refreshStory.mutate()} refreshingStory={refreshStory.isPending} storyNotice={storyNotice} /></div>
          <footer className="rail-footer"><ClockIcon period={worldClock(campaign).time_of_day} /><span>{worldClock(campaign).label || "Time unmarked"}</span>
            {!!campaign.objectives.filter((objective) => objective.status === "active").length && <button type="button" className="rail-quest-count" onClick={() => setPanel("quests")}><Flag size={12} />{campaign.objectives.filter((objective) => objective.status === "active").length}</button>}</footer>
        </aside>
        {mobileNavOpen && <button className="rail-scrim" aria-label="Close campaign notes" onClick={() => setMobileNavOpen(false)} />}

        <section className="story-workspace" aria-label="Campaign story">
          {adaptiveMood && <Ambience mood={sceneMood(campaign).mood} period={worldClock(campaign).time_of_day} intensity={sceneMood(campaign).intensity} />}
          <div className="story-meta">
            <div className="story-place"><span>{campaign.current_location || "THE OPENING SCENE"}</span><i aria-hidden="true">·</i><span>{worldClock(campaign).label || "NOW"}</span></div>
            <div className="story-meta-right"><span>TURN {String(campaign.turns.at(-1)?.turn_index ?? 0).padStart(2, "0")}</span><span>{campaign.branch.name}</span></div>
          </div>
          <div className="story-reader" ref={readerRef} onScroll={onReaderScroll}>
            <div className="story-column-inner">
              {campaign.turns.length === 0 && !liveText && !generating && (
                <div className="opening-state">
                  <AtlasArtwork compact />
                  <p className="section-overline">THE PAGE IS WAITING</p>
                  <h1>{modelReady ? "The world is ready." : "Your world is saved."}</h1>
                  <p>{modelReady ? "The first scene will begin in a moment." : "Start your local model to bring its first scene to life."}</p>
                  {modelReady ? <button className="text-button" onClick={() => { startedOpening.current = true; void runStream(OPENING_ACTION); }}>Open the first scene <ArrowUpRight size={15} /></button> : <button className="text-button" onClick={() => setSettingsOpen(true)}>Configure local inference <Settings2 size={15} /></button>}
                </div>
              )}
              {campaign.turns.map((turn, index) => <motion.article className={`story-turn${turn.id === freshTurnId ? " story-turn--fresh" : ""}`} key={turn.id}
                initial={turn.id === freshTurnId ? { opacity: 0.4, y: 10 } : false} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.45, ease: [0.22, 1, 0.36, 1] }}>
                <div className="passage-head"><span>{turn.in_world_time || (index === 0 ? "THE BEGINNING" : `TURN ${String(turn.turn_index).padStart(2, "0")}`)}</span><span className="passage-index">{String(turn.turn_index).padStart(2, "0")}</span></div>
                {turn.player_action && turn.player_action !== OPENING_ACTION && <div className="player-intention"><span>{campaign.protagonist_name}</span><p>{turn.player_action}</p></div>}
                <div className="gm-prose"><Markdown content={turn.gm_response} /></div>
                <TurnChanges changes={turn.changes ?? []} fresh={turn.id === freshTurnId} />
                <MemoryActionMenu campaign={campaign} turnId={turn.id} branchId={campaign.branch.id} content={turn.gm_response}
                  onChanged={(detail) => { cache.setQueryData(["campaign", campaignId, requestedBranchId], detail); void refetchCampaign(); }}
                  onError={(message) => setNotice(message)} />
                <div className="passage-space" aria-hidden="true"><span /></div>
              </motion.article>)}
              {campaign.game_mode === "guided" && latestChoices.length > 0 && !generating && !awaitingStoryRefresh && <section className="story-choices" aria-label="Suggested next actions">
                <div className="story-choices-head"><h2>Possible moves</h2><p>Choose one or write your own below.</p></div>
                <div className="story-choices-list">{latestChoices.map((choice, index) =>
                  <button type="button" className="story-choice" key={`${index}-${choice}`} disabled={!modelReady || changeGameMode.isPending} onClick={() => { setAction(""); void runStream(choice); }}>
                    <span className="story-choice-index" aria-hidden="true">{String(index + 1).padStart(2, "0")}</span><span>{choice}</span>
                  </button>)}</div>
              </section>}
              {campaign.game_mode === "guided" && latestTurn && latestChoices.length === 0 && !generating && !awaitingStoryRefresh &&
                <div className="story-choices-empty"><span>No choices for this scene. You can write your own action.</span>
                  <button type="button" className="text-button" disabled={!modelReady || changeGameMode.isPending} onClick={() => changeGameMode.mutate("guided")}>Try suggestions again</button>
                </div>}
              {liveText && <motion.article className="story-turn story-turn--live" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}>
                <div className="passage-head"><span>THE WORLD ANSWERS</span><span className="live-mark">{stage === "interpreting" ? "RECORDING" : "WRITING"}</span></div>
                <div className="gm-prose"><Markdown content={liveText} /></div>
                {stage !== "interpreting" && <span className="stream-caret" aria-hidden="true" />}
              </motion.article>}
              <AnimatePresence>{generating && <GenerationProgress key="progress" stage={stage === "" ? "reading" : stage}
                hasText={Boolean(liveText)} onStop={() => abortRef.current?.abort()} />}</AnimatePresence>
              {streamError && <div className="stream-error" role="alert"><CircleAlert size={16} /><p>{streamError}</p>{retryRequest && <button className="text-button" onClick={() => void runStream(retryRequest.action, retryRequest.instruction, retryRequest.targetTurnId)}>Try again</button>}</div>}
              {notice && <p className="notice-line notice-line--error" role="status">{notice}</p>}
            </div>
          </div>
          {showJump && <button className="jump-latest" onClick={() => { setIsFollowing(true); if (readerRef.current) readerRef.current.scrollTop = readerRef.current.scrollHeight; }}>Jump to latest <ArrowUpRight size={14} /></button>}
          <form className="action-dock" onSubmit={(event) => void sendAction(event)}>
            <div className="action-dock-head">
              <label htmlFor="player-action">Your next action</label>
              <div className="dock-settings"><label className="play-mode-field" htmlFor="world-mood"><span>World mood</span>
                <select id="world-mood" value={campaign.theme?.family ?? "neutral"} onChange={(event) => changeTheme.mutate(event.target.value as ThemeFamily)} disabled={generating || changeTheme.isPending}>
                  <option value="dark_fantasy">Dark fantasy</option><option value="horror">Horror</option><option value="mystery">Mystery</option><option value="cozy">Cozy</option><option value="romance">Romance</option><option value="cyberpunk">Cyberpunk</option><option value="sci_fi">Science fiction</option><option value="survival">Survival</option><option value="modern">Modern</option><option value="neutral">Neutral</option>
                </select>
              </label><label className="play-mode-field adaptive-toggle" htmlFor="adaptive-mood"><span>Adaptive</span>
                <input id="adaptive-mood" type="checkbox" checked={adaptiveMood} onChange={(event) => setAdaptiveMood(event.target.checked)} />
              </label><label className="play-mode-field" htmlFor="play-mode"><span>Play style</span>
                <select id="play-mode" value={campaign.game_mode} onChange={(event) => changeGameMode.mutate(event.target.value as GameMode)} disabled={generating || changeGameMode.isPending}>
                  <option value="freeform">Write actions</option><option value="guided">Offer choices</option>
                </select>
              </label></div>
            </div>
            <div className="action-dock-entry">
              <textarea id="player-action" value={action} onChange={(event) => setAction(event.target.value)} onKeyDown={(event: KeyboardEvent<HTMLTextAreaElement>) => {
                if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void sendAction(event as unknown as FormEvent); }
              }} placeholder="What do you do?" rows={1} disabled={generating || !modelReady || changeGameMode.isPending || changeTheme.isPending} />
              {generating ? <button type="button" className="send-button send-button--stop" aria-label="Stop generation" onClick={() => abortRef.current?.abort()}><StopCircle size={18} /></button> : <button type="submit" className="send-button" aria-label="Send action" disabled={!action.trim() || !modelReady || changeGameMode.isPending || changeTheme.isPending}><Send size={17} /></button>}
            </div>
            <div className="dock-foot"><span>Enter to act · Shift + Enter for a new line</span><span>{changeGameMode.isPending ? "Preparing play style…" : modelReady ? campaign.game_mode === "guided" ? "You can always write your own action" : "Free-form action" : "Model required"}</span></div>
          </form>
        </section>
      </div>
      <ChangeToasts latest={latestChanges} />
      <ModelSettingsDialog open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </main>
  );
}
