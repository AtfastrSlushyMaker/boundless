"use client";

import Link from "next/link";
import Image from "next/image";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ArrowLeft, ArrowUpRight, Backpack, ChevronDown, CircleAlert, Clock3, GitBranch,
  Map, Menu, MessageSquareText, PanelLeftClose, PanelLeftOpen, Pencil, RotateCcw,
  ScrollText, Send, Settings2, Shield, StopCircle, Users, X,
} from "lucide-react";
import { FormEvent, KeyboardEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AtlasArtwork } from "@/components/AtlasArtwork";
import { ModelSettingsDialog } from "@/components/ModelSettingsDialog";
import { PeoplePanel } from "@/components/PeoplePanel";
import { api, CampaignDetail, GameMode, StreamEvent, ThemeFamily, portraitUrl, streamTurn } from "@/lib/api";

const OPENING_ACTION = "Open on the campaign's stated starting moment.";
type LorePanel = "character" | "people" | "inventory" | "world" | "rules" | "journal";
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

function SidebarContent({ panel, campaign, onRefreshSetup, refreshingSetup, refreshedSetup,
                          onReindexPeople, reindexingPeople, reindexedPeople, onRefresh }: {
  panel: LorePanel; campaign: CampaignDetail; onRefreshSetup: () => void; refreshingSetup: boolean; refreshedSetup: boolean;
  onReindexPeople: () => void; reindexingPeople: boolean; reindexedPeople: boolean;
  onRefresh: () => void;
}) {
  const [characterTab, setCharacterTab] = useState<"overview" | "traits" | "history" | "goals">("overview");
  const [inventorySearch, setInventorySearch] = useState("");
  const [journalTab, setJournalTab] = useState<"events" | "memories" | "secrets">("events");
  if (panel === "character") {
    const constitution = campaign.constitution;
    const abilities = [...((constitution.abilities as string[] | undefined) ?? []), ...((constitution.powers as string[] | undefined) ?? [])].filter((value, index, all) => all.indexOf(value) === index);
    const protagonist = campaign.characters.find((person) => person.name === campaign.protagonist_name);
    const identity = (protagonist?.attributes ?? ((constitution.starting_state as Record<string, unknown> | undefined)?.identity as Record<string, unknown> | undefined) ?? {});
    const money = (campaign.current_state.money ?? protagonist?.attributes?.money) as { amount?: number; currency?: string } | undefined;
    const identityDetails = ([ ["Sex", identity.sex], ["Gender", identity.gender], ["Pronouns", identity.pronouns] ] as const)
      .filter(([, value]) => typeof value === "string").map(([label, value]) => `${label}: ${value}`);
    const portrait = portraitUrl(protagonist?.attributes?.avatar_url);
    const lists = { traits: constitution.traits as string[] | undefined, history: constitution.history as string[] | undefined,
      goals: constitution.preferences as string[] | undefined };
    return <div className="lore-content character-sheet">
      <p className="lore-label">YOUR CHARACTER</p>
      <div className="character-sheet-head"><div className="character-sheet-portrait">{portrait ? <Image src={portrait} alt="" width={72} height={72} unoptimized /> : <span>{campaign.protagonist_name.charAt(0)}</span>}</div><div><h2 className="lore-name">{campaign.protagonist_name}</h2><p>{protagonist?.role || "Player character"}</p></div></div>
      <nav className="sheet-tabs" aria-label="Character details">{(["overview", "traits", "history", "goals"] as const).map((tab) => <button type="button" key={tab} aria-current={characterTab === tab ? "page" : undefined} onClick={() => setCharacterTab(tab)}>{tab[0].toUpperCase() + tab.slice(1)}</button>)}</nav>
      {characterTab === "overview" && <>
        <dl className="character-facts">{identityDetails.map((entry) => { const [label, value] = entry.split(": "); return <div key={label}><dt>{label}</dt><dd>{value}</dd></div>; })}
          {money && typeof money.amount === "number" && <div><dt>Money</dt><dd>{money.amount.toLocaleString()} {money.currency ?? ""}</dd></div>}
          <div><dt>Status</dt><dd>{String(campaign.current_state.player_status ?? "alive")}</dd></div></dl>
        {protagonist?.personality && <section className="lore-group"><h3>Personality</h3><p className="lore-copy">{protagonist.personality}</p></section>}
        {!!abilities.length && <section className="lore-group"><h3>Established abilities</h3><ul className="plain-list">{abilities.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul></section>}
        {!!(constitution.limitations as string[] | undefined)?.length && <section className="lore-group"><h3>Limitations</h3><ul className="plain-list">{(constitution.limitations as string[]).map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul></section>}
      </>}
      {characterTab !== "overview" && <section className="lore-group"><h3>{characterTab[0].toUpperCase() + characterTab.slice(1)}</h3>{lists[characterTab]?.length ? <ul className="plain-list">{lists[characterTab].map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul> : <p className="lore-copy">Nothing recorded yet.</p>}</section>}
      <button type="button" className="text-button" onClick={onRefreshSetup} disabled={refreshingSetup}>{refreshingSetup ? "Reading premise…" : refreshedSetup ? "Details refreshed · run again" : "Refresh details from premise"}</button>
    </div>;
  }
  if (panel === "people") return <PeoplePanel campaign={campaign} onReindex={onReindexPeople}
    rebuilding={reindexingPeople} rebuilt={reindexedPeople} onRefresh={onRefresh} />;
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
  if (panel === "rules") return <div className="lore-content"><p className="lore-label">WORLD CONSTITUTION</p><h2 className="lore-name">Rules that hold</h2>
    <p className="lore-copy">These come from your original premise and explicit canon changes.</p>
    <ul className="rule-list">{campaign.canon_rules.map((rule, index) => <li key={rule.id ?? index}><Shield size={15} /><span>{rule.statement}</span></li>)}</ul>
    <details className="prompt-record"><summary>Original premise</summary><p>{campaign.original_prompt}</p></details>
  </div>;
  return <div className="lore-content journal-sheet"><p className="lore-label">CAMPAIGN RECORD</p><h2 className="lore-name">Journal</h2>
    {campaign.summary && <section className="summary-note"><h3>What has happened</h3><p>{campaign.summary}</p></section>}
    <nav className="sheet-tabs" aria-label="Journal entries">{(["events", "memories", "secrets"] as const).map((tab) => <button type="button" key={tab} aria-current={journalTab === tab ? "page" : undefined} onClick={() => setJournalTab(tab)}>{tab[0].toUpperCase() + tab.slice(1)}</button>)}</nav>
    {campaign.events.length === 0 && campaign.memories.length === 0 && !campaign.summary && <p className="lore-copy">Important events will gather here as the story unfolds.</p>}
    {journalTab === "events" && <ul className="journal-list">{campaign.events.map((event, index) => <li key={event.id ?? index}><span>{event.certainty ?? "RECORDED"}</span><p>{event.content}</p></li>)}</ul>}
    {journalTab === "memories" && <ul className="journal-list">{campaign.memories.map((memory, index) => <li key={String(memory.id ?? index)}><span>{String(memory.memory_type ?? "MEMORY")}</span><p>{String(memory.content ?? "")}</p></li>)}</ul>}
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
        if (event.type === "delta") setLiveText((current) => current + event.text);
        if (event.type === "replace") setLiveText(event.text);
        if (event.type === "error") { setStreamError(event.message); setRetryRequest(currentRequest); setGenerating(false); setLiveText(""); }
        if (event.type === "complete") {
          setGenerating(false); setLiveText(""); setRetryRequest(null); setAwaitingStoryRefresh(true);
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
      setGenerating(false); abortRef.current = null;
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
    ["character", "Character", Users], ["people", "People", MessageSquareText],
    ["inventory", "Inventory", Backpack], ["world", "World", Map], ["rules", "World rules", Shield], ["journal", "Journal", ScrollText],
  ] as const, []);

  if (campaignQuery.isLoading) return <main className="game-loading"><span className="brand-glyph">B</span><p>Opening the archive…</p></main>;
  if (campaignQuery.isError || !campaign) return <main className="game-load-error"><Link href="/" className="back-link"><ArrowLeft size={16} />Back to Boundless</Link><CircleAlert size={25} /><h1>This world could not be opened.</h1><p>{campaignQuery.error?.message ?? "Campaign not found."}</p><button className="quiet-button" onClick={() => void campaignQuery.refetch()}>Try again</button></main>;

  return (
    <main className={`game-shell theme-${campaign.theme?.family ?? "neutral"}`}>
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
          <div className="rail-content"><SidebarContent panel={panel} campaign={campaign} onRefreshSetup={() => refreshSetup.mutate()} refreshingSetup={refreshSetup.isPending} refreshedSetup={refreshSetup.isSuccess} onReindexPeople={() => reindexPeople.mutate()} reindexingPeople={reindexPeople.isPending} reindexedPeople={reindexPeople.isSuccess} onRefresh={refetchCampaign} /></div>
          <footer className="rail-footer"><Clock3 size={13} /><span>{String(campaign.current_state.world_time ?? "Time unmarked")}</span></footer>
        </aside>
        {mobileNavOpen && <button className="rail-scrim" aria-label="Close campaign notes" onClick={() => setMobileNavOpen(false)} />}

        <section className="story-workspace" aria-label="Campaign story">
          <div className="story-meta">
            <div className="story-place"><span>{campaign.current_location || "THE OPENING SCENE"}</span><i aria-hidden="true">·</i><span>{String(campaign.current_state.world_time ?? "NOW")}</span></div>
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
              {campaign.turns.map((turn, index) => <article className="story-turn" key={turn.id}>
                <div className="passage-head"><span>{turn.in_world_time || (index === 0 ? "THE BEGINNING" : `TURN ${String(turn.turn_index).padStart(2, "0")}`)}</span><span className="passage-index">{String(turn.turn_index).padStart(2, "0")}</span></div>
                {turn.player_action && turn.player_action !== OPENING_ACTION && <div className="player-intention"><span>{campaign.protagonist_name}</span><p>{turn.player_action}</p></div>}
                <div className="gm-prose"><Markdown content={turn.gm_response} /></div>
                <MemoryActionMenu campaign={campaign} turnId={turn.id} branchId={campaign.branch.id} content={turn.gm_response}
                  onChanged={(detail) => { cache.setQueryData(["campaign", campaignId, requestedBranchId], detail); void refetchCampaign(); }}
                  onError={(message) => setNotice(message)} />
                <div className="passage-space" aria-hidden="true"><span /></div>
              </article>)}
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
              {liveText && <article className="story-turn story-turn--live"><div className="passage-head"><span>THE WORLD ANSWERS</span><span className="live-mark">WRITING</span></div><div className="gm-prose"><Markdown content={liveText} /></div><span className="stream-caret" aria-hidden="true" /></article>}
              {generating && !liveText && <div className="generation-state"><span className="generation-orbit" aria-hidden="true">B</span><p>Finding what the world does next</p><button className="text-button" onClick={() => abortRef.current?.abort()}><StopCircle size={15} />Stop</button></div>}
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
      <ModelSettingsDialog open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </main>
  );
}
