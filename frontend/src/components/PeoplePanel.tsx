"use client";

import Image from "next/image";
import { useQuery } from "@tanstack/react-query";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { FormEvent, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { faceCrop, PortraitButton } from "@/components/PortraitLightbox";
import { EdgeStyle, RelationshipGraph } from "@/components/RelationshipGraph";
import { api, portraitUrl } from "@/lib/api";
import type { CampaignDetail, Character, Importance, Relationship, RelationshipEvent } from "@/lib/api";

type Props = {
  campaign: CampaignDetail;
  onReindex: () => void;
  rebuilding: boolean;
  rebuilt: boolean;
  onRefresh: () => void;
};

type PeopleFilter = "all" | "allies" | "enemies" | "factions";
type RelationshipFilter = "all" | "kinship" | Axis;
type Axis = "trust" | "respect" | "fear" | "hostility" | "affection" | "loyalty" | "attraction" | "debt" | "dependence";
type ProfileDraft = { role: string; personality: string; appearance: string; sex: string; gender: string; pronouns: string };
type CoreAxis = "trust" | "respect" | "fear" | "hostility";
type RelationDraft = Record<CoreAxis, string> & { status: string; summary: string };

const CORE_AXES: Axis[] = ["trust", "respect", "fear", "hostility"];
const AXES: Axis[] = ["trust", "respect", "fear", "hostility", "affection", "loyalty", "attraction", "debt", "dependence"];
const AXIS_LABELS: Record<Axis, string> = { trust: "Trust", respect: "Respect", fear: "Fear", hostility: "Hostility",
  affection: "Affection", loyalty: "Loyalty", attraction: "Attraction", debt: "Debt", dependence: "Dependence" };
const IMPORTANCE_ORDER: Importance[] = ["COMPANION", "MAJOR", "RECURRING", "MINOR", "BACKGROUND"];
const IMPORTANCE_LABEL: Record<Importance, string> = { COMPANION: "Companion", MAJOR: "Major", RECURRING: "Recurring", MINOR: "Minor", BACKGROUND: "Background" };

function importanceOf(person: Character): Importance {
  return person.importance && IMPORTANCE_ORDER.includes(person.importance) ? person.importance : "MINOR";
}

function eventLabel(event: RelationshipEvent) {
  const axis = AXES.includes(event.dimension as Axis) ? AXIS_LABELS[event.dimension as Axis] : event.dimension.replaceAll("_", " ");
  if (event.delta === null || event.delta === undefined || !Number.isFinite(event.delta)) return axis === "note" ? "" : axis;
  const rounded = Math.round(event.delta);
  return `${axis} ${rounded > 0 ? "↑" : rounded < 0 ? "↓" : "·"}${Math.abs(rounded)}`;
}

function RelationshipTimeline({ relation }: { relation: Relationship | undefined }) {
  const events = (relation?.events ?? []).filter((event) => event.reason || event.delta);
  const legacy = historyFor(relation);
  if (!events.length && !legacy.length) return <p className="empty-relationship-note">No meaningful changes recorded yet.</p>;
  return <ol className="relationship-timeline">
    {events.map((event) => <li key={event.id} data-direction={(event.delta ?? 0) > 0 ? "up" : (event.delta ?? 0) < 0 ? "down" : "flat"}>
      <span className="timeline-turn">{event.turn_index !== null ? `Turn ${event.turn_index}` : "Earlier"}</span>
      {eventLabel(event) && <strong>{eventLabel(event)}</strong>}
      {event.reason && <p>{event.reason}{event.location ? ` · ${event.location}` : ""}</p>}
    </li>)}
    {!events.length && legacy.map((entry, index) => <li key={`${entry.turn_id ?? "turn"}-${index}`}>
      <span className="timeline-turn">{entry.turn_index !== undefined ? `Turn ${entry.turn_index}` : "Earlier"}</span><p>{entry.reason}</p></li>)}
  </ol>;
}

function isIndividual(person: Character, protagonistName: string) {
  return person.name === protagonistName || !/\b(?:guards|priests|soldiers|villagers|citizens)\b/i.test(person.name);
}

const NOISE = /^(?:first )?appeared in the story$/i;

function score(relation: Relationship | undefined, axis: Axis) {
  const value = relation?.dimensions?.[axis];
  return typeof value === "number" && Number.isFinite(value) ? Math.max(0, Math.min(100, value)) : null;
}

function socialRelation(person: Character | undefined, protagonistName: string, relationships: Relationship[]) {
  if (!person || person.name === protagonistName) return undefined;
  const personView = relationships.find((relation) => relation.from === person.name && relation.to === protagonistName);
  const playerView = relationships.find((relation) => relation.from === protagonistName && relation.to === person.name);
  const detailCount = (relation: Relationship | undefined) => {
    if (!relation) return -1;
    const dimensions = relation.dimensions ?? {};
    const axisCount = AXES.reduce((count, axis) => count + (typeof dimensions[axis] === "number" ? 1 : 0), 0);
    const historyCount = Array.isArray(dimensions.history) ? dimensions.history.length : 0;
    return axisCount * 1000 + historyCount * 10 + (dimensions.last_interaction ? 1 : 0);
  };
  return detailCount(playerView) > detailCount(personView) ? playerView : personView ?? playerView;
}

function categoryFor(person: Character, protagonistName: string, relationships: Relationship[]): PeopleFilter {
  const relation = socialRelation(person, protagonistName, relationships);
  const hostility = score(relation, "hostility") ?? 0;
  const trust = score(relation, "trust") ?? 0;
  if (hostility >= 60) return "enemies";
  if (trust >= 70 && hostility < 30) return "allies";
  return "all";
}

function isFactionMember(person: Character) {
  return Boolean(person.attributes?.faction || person.attributes?.faction_name)
    || /\b(?:faction|clan|order|guild|house|guard|watch|circle|court)\b/i.test(person.role);
}

function relationshipStatus(relation: Relationship | undefined) {
  const explicit = relation?.dimensions?.status;
  if (typeof explicit === "string" && explicit.trim()) return explicit.trim();
  const trust = score(relation, "trust");
  const respect = score(relation, "respect");
  const fear = score(relation, "fear");
  const hostility = score(relation, "hostility");
  if (hostility !== null && hostility >= 70) return "Hostile";
  if (trust !== null && trust >= 75 && (respect ?? 0) >= 55 && (hostility ?? 0) < 25) return "Trusted ally";
  if (fear !== null && fear >= 70) return "Feared presence";
  if (hostility !== null && hostility >= 40) return "Tense relationship";
  return "Known acquaintance";
}

function edgeAxis(relation: Relationship): Axis | null {
  const values = AXES.map((axis) => ({ axis, value: score(relation, axis) })).filter(
    (entry): entry is { axis: Axis; value: number } => entry.value !== null,
  );
  if (!values.length) return null;
  return values.sort((left, right) => right.value - left.value)[0].axis;
}

function connectionLabel(relation: Relationship, selectedName: string) {
  const other = relation.from === selectedName ? relation.to : relation.from;
  const kinship = relation.dimensions?.kinship;
  if (kinship === "parent") return relation.to === selectedName ? `Parent: ${other}` : `Child: ${other}`;
  if (kinship === "child") return relation.from === selectedName ? `Parent: ${other}` : `Child: ${other}`;
  const axis = edgeAxis(relation);
  const value = axis ? score(relation, axis) : null;
  return `${other}: ${axis ? `${AXIS_LABELS[axis].toLowerCase()} ${value}` : relation.summary || "Known connection"}`;
}

function relationMatches(relation: Relationship, filter: RelationshipFilter) {
  if (filter === "all") return true;
  if (filter === "kinship") return Boolean(relation.dimensions?.kinship);
  return score(relation, filter) !== null;
}

function recordLocation(relation: Relationship | undefined) {
  const interaction = relation?.dimensions?.last_interaction;
  if (typeof interaction?.location === "string") return interaction.location;
  const history = relation?.dimensions?.history;
  if (Array.isArray(history)) {
    const last = [...history].reverse().find((entry) => entry && typeof entry === "object" && "location" in entry);
    if (last && typeof last.location === "string") return last.location;
  }
  return "";
}

function historyFor(relation: Relationship | undefined) {
  const history = relation?.dimensions?.history;
  return Array.isArray(history) ? history.filter((entry) => entry && typeof entry === "object" && typeof entry.reason === "string" && !NOISE.test(entry.reason)) : [];
}

function displayRelationType(relation: Relationship) {
  const kind = relation.dimensions?.kinship;
  if (kind) return String(kind).replaceAll("_", " ");
  const axis = edgeAxis(relation);
  return axis ? AXIS_LABELS[axis] : relation.summary || "Known";
}

function factsFor(person: Character): Array<{ content: string; type?: string; turn?: number | null }> {
  if (person.facts?.length) return person.facts.map((fact) => ({ content: fact.content, type: fact.type, turn: fact.turn_index }));
  const facts = person.attributes?.known_facts;
  return Array.isArray(facts) ? facts.filter((fact): fact is string => typeof fact === "string" && fact.trim().length > 0).map((content) => ({ content })) : [];
}

export function PeoplePanel({ campaign, onReindex, rebuilding, rebuilt, onRefresh }: Props) {
  const [view, setView] = useState<"list" | "graph">("list");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [peopleFilter, setPeopleFilter] = useState<PeopleFilter>("all");
  const [relationshipFilter, setRelationshipFilter] = useState<RelationshipFilter>("all");
  const [search, setSearch] = useState("");
  const [showBackground, setShowBackground] = useState(false);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [showAllFacts, setShowAllFacts] = useState(false);
  const reduceMotion = useReducedMotion();
  const [avatarJob, setAvatarJob] = useState<{ characterId: string; id: string } | null>(null);
  const [avatarError, setAvatarError] = useState("");
  const [requestingAvatar, setRequestingAvatar] = useState(false);
  const avatarInputRef = useRef<HTMLInputElement>(null);
  const imageSettings = useQuery({ queryKey: ["image-settings"], queryFn: api.imageSettings, retry: false });
  const [editing, setEditing] = useState<"profile" | "relationship" | null>(null);
  const [saving, setSaving] = useState(false);
  const [profileDraft, setProfileDraft] = useState<ProfileDraft>({ role: "", personality: "", appearance: "", sex: "", gender: "", pronouns: "" });
  const [relationDraft, setRelationDraft] = useState<RelationDraft>({ trust: "", respect: "", fear: "", hostility: "", status: "", summary: "" });
  const choosePerson = (id: string) => { setSelectedId(id); setSelectedEdgeId(null); setShowAllFacts(false); setEditing(null); setAvatarError(""); };

  const protagonist = campaign.characters.find((person) => person.name === campaign.protagonist_name);
  const people = campaign.characters.filter((person) => person.name !== campaign.protagonist_name && isIndividual(person, campaign.protagonist_name))
    .sort((left, right) => IMPORTANCE_ORDER.indexOf(importanceOf(left)) - IMPORTANCE_ORDER.indexOf(importanceOf(right)) || left.name.localeCompare(right.name));
  const backgroundCount = people.filter((person) => importanceOf(person) === "BACKGROUND").length;
  const matchingPeople = people.filter((person) => {
    const category = categoryFor(person, campaign.protagonist_name, campaign.relationships);
    const faction = person.attributes?.faction ?? person.attributes?.faction_name;
    const inFaction = isFactionMember(person);
    const matchesFilter = peopleFilter === "all" || (peopleFilter === "factions" ? inFaction : category === peopleFilter);
    const query = search.trim().toLocaleLowerCase();
    const matchesSearch = !query || `${person.name} ${person.role} ${String(faction ?? "")}`.toLocaleLowerCase().includes(query);
    const matchesImportance = showBackground || importanceOf(person) !== "BACKGROUND" || selectedId === person.id;
    return matchesFilter && matchesSearch && matchesImportance;
  });
  const visible = new Map<string, Character>();
  if (protagonist) visible.set(protagonist.name, protagonist);
  matchingPeople.forEach((person) => visible.set(person.name, person));
  const graphPeople = [...visible.values()];
  const selected = graphPeople.find((person) => person.id === selectedId) ?? matchingPeople[0] ?? protagonist;
  const graphNames = new Set(graphPeople.map((person) => person.name));
  const relationships = campaign.relationships.filter((relation) =>
    graphNames.has(relation.from) && graphNames.has(relation.to) && relationMatches(relation, relationshipFilter));
  const connections = selected ? campaign.relationships.filter((relation) =>
    (relation.from === selected.name || relation.to === selected.name) && relationMatches(relation, relationshipFilter)) : [];
  const selectedEdge = campaign.relationships.find((relation) => relation.id === selectedEdgeId);
  const selectedRelation = socialRelation(selected, campaign.protagonist_name, campaign.relationships);
  const focused = selectedId !== null && selected?.id === selectedId;
  const presentAxes = AXES.filter((axis) => !CORE_AXES.includes(axis) && campaign.relationships.some((relation) => score(relation, axis) !== null));
  const lastInteraction = selectedRelation?.dimensions?.last_interaction;
  const knownFacts = selected ? factsFor(selected) : [];
  const pendingAvatarJob = selected && (avatarJob?.characterId === selected.id ? avatarJob.id : selected.attributes?.avatar_job);
  const avatarUrl = selected ? portraitUrl(selected.attributes?.avatar_url) : "";
  const imageProvider = imageSettings.data?.enabled ? imageSettings.data.provider : "none";

  const editProfile = () => {
    if (!selected) return;
    setAvatarError("");
    setProfileDraft({
      role: selected.role ?? "", personality: selected.personality ?? "",
      appearance: String(selected.attributes?.appearance ?? ""), sex: String(selected.attributes?.sex ?? ""),
      gender: String(selected.attributes?.gender ?? ""), pronouns: String(selected.attributes?.pronouns ?? ""),
    });
    setEditing("profile");
  };
  const editRelationship = () => {
    if (!selectedRelation) return;
    setAvatarError("");
    setRelationDraft({
      trust: score(selectedRelation, "trust")?.toString() ?? "",
      respect: score(selectedRelation, "respect")?.toString() ?? "",
      fear: score(selectedRelation, "fear")?.toString() ?? "",
      hostility: score(selectedRelation, "hostility")?.toString() ?? "",
      status: String(selectedRelation.dimensions?.status ?? ""), summary: selectedRelation.summary ?? "",
    });
    setEditing("relationship");
  };
  const saveEdit = async (event: FormEvent) => {
    event.preventDefault();
    if (!selected || !editing || saving) return;
    setSaving(true);
    setAvatarError("");
    try {
      if (editing === "profile") {
        await api.updateCharacter(campaign.id, campaign.branch.id, selected.id, profileDraft);
      } else if (selectedRelation) {
        const axis = (key: CoreAxis) => relationDraft[key] === "" ? null : Number(relationDraft[key]);
        await api.updateRelationship(campaign.id, campaign.branch.id, selectedRelation.id, {
          trust: axis("trust"), respect: axis("respect"), fear: axis("fear"), hostility: axis("hostility"),
          status: relationDraft.status, summary: relationDraft.summary,
        });
      }
      setEditing(null);
      onRefresh();
    } catch (error) {
      setAvatarError(error instanceof Error ? error.message : "Changes could not be saved.");
    } finally { setSaving(false); }
  };

  useEffect(() => {
    if (!selected || typeof pendingAvatarJob !== "string" || !pendingAvatarJob) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const status = await api.avatarStatus(campaign.id, campaign.branch.id, selected.id);
        if (cancelled) return;
        if (status.done) {
          setAvatarJob(null);
          setAvatarError("");
          onRefresh();
        } else if (status.status === "FAILED" || status.status === "CANCELLED") {
          setAvatarJob(null);
          setAvatarError(status.error || "Portrait generation stopped. You can retry it.");
          onRefresh();
        } else {
          timer = setTimeout(poll, 8000);
        }
      } catch {
        if (!cancelled) timer = setTimeout(poll, 15000);
      }
    };
    timer = setTimeout(poll, 8000);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [campaign.id, campaign.branch.id, selected, pendingAvatarJob, onRefresh]);

  const generateAvatar = async (newSeed = false) => {
    if (!selected || requestingAvatar) return;
    setAvatarError("");
    setRequestingAvatar(true);
    try {
      const job = await api.generateAvatar(campaign.id, campaign.branch.id, selected.id, newSeed);
      setAvatarJob({ characterId: selected.id, id: job.job_id });
      onRefresh();
    } catch (error) {
      setAvatarError(error instanceof Error ? error.message : "Portrait request failed.");
    } finally { setRequestingAvatar(false); }
  };

  const uploadAvatar = async (file: File | undefined) => {
    if (!selected || !file) return;
    setRequestingAvatar(true); setAvatarError("");
    try { await api.uploadAvatar(campaign.id, campaign.branch.id, selected.id, file); onRefresh(); }
    catch (error) { setAvatarError(error instanceof Error ? error.message : "Portrait upload failed."); }
    finally { setRequestingAvatar(false); if (avatarInputRef.current) avatarInputRef.current.value = ""; }
  };

  const removeAvatar = async () => {
    if (!selected) return;
    setRequestingAvatar(true); setAvatarError("");
    try { await api.removeAvatar(campaign.id, campaign.branch.id, selected.id); onRefresh(); }
    catch (error) { setAvatarError(error instanceof Error ? error.message : "Portrait could not be removed."); }
    finally { setRequestingAvatar(false); }
  };

  useEffect(() => {
    if (view !== "graph") return;
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") setView("list"); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [view]);
  const layoutKey = `boundless:graph-positions:${campaign.id}:${campaign.branch.id}`;
  const edgeStyle = (relation: Relationship): EdgeStyle => {
    const axis = relationshipFilter !== "all" && relationshipFilter !== "kinship" ? relationshipFilter : edgeAxis(relation);
    const label = axis && score(relation, axis) !== null ? `${AXIS_LABELS[axis]} ${score(relation, axis)}`
      : typeof relation.dimensions?.kinship === "string" ? relation.dimensions.kinship
        : relation.dimensions?.awareness ? "" : displayRelationType(relation);
    return { axis, label, width: axis ? 1 + ((score(relation, axis) ?? 0) / 100) * 2.2 : 1.1 };
  };

  const detail = selected && <section className="person-detail" aria-live="polite">
    <div className="person-detail-head"><div className="person-identity">
      {avatarUrl ? <PortraitButton className={`person-portrait${faceCrop(selected.attributes).className}`} style={faceCrop(selected.attributes).style} src={avatarUrl} name={selected.name} caption={selected.role}>
        <Image src={avatarUrl} alt="" width={68} height={68} unoptimized /></PortraitButton>
        : <div className="person-portrait" aria-label={`No portrait for ${selected.name}`}><span aria-hidden="true">{selected.name.charAt(0).toLocaleUpperCase()}</span></div>}<div><p className="person-detail-role">{selected.role || "Role unknown"}</p><h3>{selected.name}</h3>
        {selected.name !== campaign.protagonist_name && <span className={`importance-badge importance-badge--${importanceOf(selected).toLowerCase()}`}>{IMPORTANCE_LABEL[importanceOf(selected)]}</span>}
        {selected.status && selected.status !== "alive" && <p className="person-status">{selected.status}</p>}</div></div></div>
    <div className="portrait-controls">
      {(imageProvider === "comfyui" || imageProvider === "ai_horde") && <button type="button" className="portrait-action" onClick={() => void generateAvatar(Boolean(avatarUrl))} disabled={requestingAvatar || Boolean(pendingAvatarJob)}>{requestingAvatar ? "Requesting…" : pendingAvatarJob ? "Portrait generating…" : avatarUrl ? "Regenerate portrait" : "Generate portrait"}</button>}
      <button type="button" className="portrait-action" onClick={() => avatarInputRef.current?.click()} disabled={requestingAvatar}>Change / upload</button>
      {avatarUrl && <button type="button" className="portrait-action" onClick={() => void removeAvatar()} disabled={requestingAvatar}>Remove</button>}
      <input ref={avatarInputRef} type="file" accept="image/png,image/jpeg,image/webp" className="visually-hidden" aria-label={`Upload portrait for ${selected.name}`} onChange={(event) => void uploadAvatar(event.target.files?.[0])} />
    </div>
    <p className="portrait-note">{pendingAvatarJob ? "Portrait queued. The story continues while it generates." : imageProvider === "comfyui" ? avatarUrl ? "Portrait saved in Boundless. Regenerate for a new take on the same look." : "Generate a portrait on your configured ComfyUI server." : imageProvider === "ai_horde" ? "Sends character appearance to AI Horde. The image is copied into Boundless storage." : imageProvider === "perchance_assisted" ? "Generate in Perchance, then upload the saved image here." : "Enable a portrait provider in Settings, or upload an image directly."}</p>
    {avatarError && <p className="portrait-error" role="alert">{avatarError}</p>}
    <button type="button" className="person-edit-action" onClick={editProfile}>Edit character details</button>
    {editing === "profile" && <form className="person-edit-form" onSubmit={(event) => void saveEdit(event)}>
      <label>Role<input maxLength={160} value={profileDraft.role} onChange={(event) => setProfileDraft({ ...profileDraft, role: event.target.value })} /></label>
      <label>Appearance<textarea rows={3} maxLength={1000} value={profileDraft.appearance} onChange={(event) => setProfileDraft({ ...profileDraft, appearance: event.target.value })} placeholder="Visual features for the portrait and story" /></label>
      <label>Personality<textarea rows={2} maxLength={2000} value={profileDraft.personality} onChange={(event) => setProfileDraft({ ...profileDraft, personality: event.target.value })} /></label>
      <div className="person-edit-grid"><label>Sex<select value={profileDraft.sex} onChange={(event) => setProfileDraft({ ...profileDraft, sex: event.target.value })}><option value="">Unspecified</option><option value="male">Male</option><option value="female">Female</option><option value="intersex">Intersex</option><option value="other">Other</option></select></label>
        <label>Gender<select value={profileDraft.gender} onChange={(event) => setProfileDraft({ ...profileDraft, gender: event.target.value })}><option value="">Unspecified</option><option value="man">Man</option><option value="woman">Woman</option><option value="nonbinary">Nonbinary</option><option value="other">Other</option></select></label></div>
      <label>Pronouns<input maxLength={60} value={profileDraft.pronouns} onChange={(event) => setProfileDraft({ ...profileDraft, pronouns: event.target.value })} placeholder="e.g. he/him" /></label>
      <div className="person-edit-actions"><button type="button" onClick={() => setEditing(null)}>Cancel</button><button type="submit" disabled={saving}>{saving ? "Saving…" : "Save details"}</button></div>
    </form>}
    {!!selected.aliases?.length && <div className="alias-row" aria-label="Also known as"><span>Also known as</span>
      {selected.aliases.map((alias) => <em key={alias.alias} title={alias.type.replaceAll("_", " ").toLowerCase()}>{alias.alias}</em>)}</div>}
    {(() => {
      const visual = (selected.attributes?.visual_identity ?? {}) as Record<string, unknown>;
      const rows = ([["Age", visual.apparent_age], ["Build", visual.build], ["Face", visual.face], ["Eyes", visual.eyes],
        ["Hair", visual.hair], ["Skin", visual.skin], ["Wearing", visual.clothing], ["Now", visual.current_state]] as Array<[string, unknown]>)
        .filter(([, value]) => typeof value === "string" && value.trim());
      const features = Array.isArray(visual.features) ? visual.features.filter((value): value is string => typeof value === "string") : [];
      if (!rows.length && !features.length) return null;
      return <div className="appearance-card"><h4>Appearance</h4>
        <dl>{rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{String(value)}</dd></div>)}</dl>
        {features.length > 0 && <div className="appearance-marks">{features.map((feature) => <span key={feature}>{feature}</span>)}</div>}
      </div>;
    })()}
    <dl>{([
      ["Gender", selected.attributes?.gender], ["Pronouns", selected.attributes?.pronouns],
      ["First met", selected.attributes?.first_meeting_place], ["Faction", selected.attributes?.faction ?? selected.attributes?.faction_name],
    ] as Array<[string, unknown]>).filter(([, value]) => typeof value === "string" && value.trim()).map(([label, value]) =>
      <div key={label}><dt>{label}</dt><dd>{String(value)}</dd></div>)}</dl>
    {selected.personality && <p>{selected.personality}</p>}
    {selected.motivations?.length > 0 && <div className="person-facts"><h4>Motives</h4><ul>{selected.motivations.map((motive, index) => <li key={`${motive}-${index}`}>{motive}</li>)}</ul></div>}
    {knownFacts.length > 0 && <div className="person-facts"><h4>Known facts <small>{knownFacts.length}</small></h4><ul className="fact-list">{(showAllFacts ? knownFacts : knownFacts.slice(-8)).map((fact, index) =>
      <li key={`${fact.content}-${index}`}>{fact.type && fact.type !== "general" && <span className={`fact-tag fact-tag--${fact.type}`}>{fact.type}</span>}{fact.content}</li>)}</ul>
      {knownFacts.length > 8 && <button type="button" className="person-edit-action" onClick={() => setShowAllFacts(!showAllFacts)}>{showAllFacts ? "Show recent facts" : `Show all ${knownFacts.length} facts`}</button>}</div>}
    <div className="relationship-readout">
      <div className="relationship-headline"><h4>Relationship with {campaign.protagonist_name}</h4>{selectedRelation && <button type="button" onClick={editRelationship}>Edit</button>}</div>
      {editing === "relationship" && <form className="person-edit-form" onSubmit={(event) => void saveEdit(event)}>
        <div className="person-edit-grid">{(CORE_AXES as CoreAxis[]).map((axis) => <label key={axis}>{AXIS_LABELS[axis]}<input type="number" min={0} max={100} placeholder="Unknown" value={relationDraft[axis]} onChange={(event) => setRelationDraft({ ...relationDraft, [axis]: event.target.value })} /></label>)}</div>
        <label>Status<select value={relationDraft.status} onChange={(event) => setRelationDraft({ ...relationDraft, status: event.target.value })}><option value="">Derived from scores</option><option value="Known acquaintance">Known acquaintance</option><option value="Trusted ally">Trusted ally</option><option value="Tense relationship">Tense relationship</option><option value="Hostile">Hostile</option><option value="Family">Family</option><option value="Stranger">Stranger</option></select></label>
        <label>Relationship note<textarea rows={2} maxLength={2000} value={relationDraft.summary} onChange={(event) => setRelationDraft({ ...relationDraft, summary: event.target.value })} /></label>
        <div className="person-edit-actions"><button type="button" onClick={() => setEditing(null)}>Cancel</button><button type="submit" disabled={saving}>{saving ? "Saving…" : "Save relationship"}</button></div>
      </form>}
      <p className="relationship-direction">{selectedRelation ? `${selectedRelation.from} → ${selectedRelation.to}` : "No relationship scores recorded"}</p>
      <div className="relationship-metrics">{[...CORE_AXES, ...AXES.filter((axis) => !CORE_AXES.includes(axis) && score(selectedRelation, axis) !== null)].map((axis) => {
        const value = score(selectedRelation, axis);
        return <div className="relationship-metric" key={axis} data-axis={axis}>
          <span>{AXIS_LABELS[axis]}</span><strong>{value === null ? "—" : value}</strong>
          <div className="relationship-meter" role="img" aria-label={`${AXIS_LABELS[axis]} ${value === null ? "not recorded" : `${value} out of 100`}`}>
            {value !== null && <span style={{ width: `${value}%` }} />}
          </div>
        </div>;
      })}</div>
      <div className="relationship-status"><span>Status</span><strong>{relationshipStatus(selectedRelation)}</strong></div>
      <div className="person-facts"><h4>What changed between them</h4><RelationshipTimeline relation={selectedRelation} /></div>
      {lastInteraction && typeof lastInteraction === "object" && <div className="person-last-interaction">
        <span>Last interaction</span>
        <strong>Turn {String(lastInteraction.turn_index ?? "—")} · {recordLocation(selectedRelation) || "Location unknown"}</strong>
      </div>}
    </div>
    {connections.length > 0 && <div className="person-facts"><h4>Other connections</h4><ul className="connection-list">{connections.filter((relation) => relation.id !== selectedRelation?.id).map((relation) =>
      <li key={relation.id}><button type="button" onClick={() => setSelectedEdgeId(relation.id)}>{connectionLabel(relation, selected.name)}</button></li>)}</ul></div>}
  </section>;
  const edgeDetail = selectedEdge && <section className="person-detail edge-detail" aria-live="polite">
    <button type="button" className="person-edit-action" onClick={() => setSelectedEdgeId(null)}>← Back to {selected?.name ?? "person"}</button>
    <p className="person-detail-role">Relationship</p>
    <h3>{selectedEdge.from} <span aria-hidden="true">→</span> {selectedEdge.to}</h3>
    {selectedEdge.summary && <p>{selectedEdge.summary}</p>}
    <div className="relationship-metrics">{AXES.filter((axis) => score(selectedEdge, axis) !== null).map((axis) => {
      const value = score(selectedEdge, axis) ?? 0;
      return <div className="relationship-metric" key={axis} data-axis={axis}><span>{AXIS_LABELS[axis]}</span><strong>{value}</strong>
        <div className="relationship-meter"><motion.span initial={{ width: 0 }} animate={{ width: `${value}%` }} transition={{ duration: reduceMotion ? 0 : 0.5 }} /></div></div>;
    })}</div>
    {typeof selectedEdge.dimensions?.kinship === "string" && <p>Family: {selectedEdge.dimensions.kinship}</p>}
    <div className="person-facts"><h4>History</h4><RelationshipTimeline relation={selectedEdge} /></div>
  </section>;

  const openGraph = () => setView("graph");
  const closeGraph = () => setView("list");

  return <div className="lore-content people-panel">
    <p className="lore-label">THE PEOPLE</p>
    <h2 className="lore-name">Known faces</h2>
    <div className="people-toolbar">
      <button type="button" className="people-open-graph" onClick={openGraph}>View connections</button>
      <button type="button" className="text-button" disabled={rebuilding} onClick={onReindex}>
        {rebuilding ? "Reading the story…" : rebuilt ? "People refreshed" : "Recover people from story"}
      </button>
    </div>
    {!people.length && <p className="lore-copy">No individual has entered the record yet. Recover people from the story to scan earlier scenes.</p>}
    {!!people.length && <div className="people-list" aria-label="Known people">
      {people.map((person) => <button type="button" key={person.id} className="person-row"
        aria-pressed={selected?.id === person.id} onClick={() => choosePerson(person.id)}>
        <strong>{person.name}</strong><span>{person.role || "Role unknown"}</span>
        <i className={`importance-dot importance-dot--${importanceOf(person).toLowerCase()}`} title={IMPORTANCE_LABEL[importanceOf(person)]} />
      </button>)}
    </div>}
    {view === "list" && detail}
    {view === "graph" && typeof document !== "undefined" && createPortal(<div className={`people-graph-scrim theme-${campaign.theme?.family ?? "neutral"}`} onMouseDown={(event) => { if (event.target === event.currentTarget) closeGraph(); }}>
      <section className="people-graph-dialog" role="dialog" aria-modal="true" aria-labelledby="people-graph-title">
        <header className="people-graph-head"><div><h2 id="people-graph-title">People and connections</h2><p>Who {campaign.protagonist_name} knows, and what has changed between them.</p></div>
          <div className="people-graph-head-actions"><button type="button" className="people-recover" disabled={rebuilding} onClick={onReindex}>{rebuilding ? "Reading story…" : rebuilt ? "People refreshed" : "Recover people"}</button>
            <button type="button" className="people-close" aria-label="Close connections" onClick={closeGraph}>Close</button></div></header>
        <div className="people-graph-layout">
          <aside className="people-index" aria-label="People filters and search">
            <label className="people-search-label" htmlFor="people-search">Search people</label>
            <input id="people-search" type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Name, role, faction" />
            <nav className="people-filters" aria-label="Filter people">
              {(["all", "allies", "enemies", "factions"] as PeopleFilter[]).map((filter) => <button type="button" key={filter}
                aria-pressed={peopleFilter === filter} onClick={() => setPeopleFilter(filter)}>{filter === "all" ? "All people" : filter[0].toUpperCase() + filter.slice(1)}
                <span>{filter === "all" ? people.length + (protagonist ? 1 : 0) : people.filter((person) => filter === "factions"
                  ? isFactionMember(person)
                  : categoryFor(person, campaign.protagonist_name, campaign.relationships) === filter).length}</span>
              </button>)}
            </nav>
            <div className="people-index-heading"><span>Campaign people</span><span>{matchingPeople.length + (protagonist ? 1 : 0)}</span></div>
            <div className="people-index-list">
              {protagonist && <button type="button" className="people-index-row" aria-pressed={selected?.id === protagonist.id} onClick={() => choosePerson(protagonist.id)}>
                <strong>{protagonist.name}</strong><span>Player character</span></button>}
              {matchingPeople.map((person) => <button type="button" className="people-index-row" key={person.id}
                aria-pressed={selected?.id === person.id} onClick={() => choosePerson(person.id)}>
                <strong>{person.name}</strong><span>{person.role || "Role unknown"}</span></button>)}
              {!matchingPeople.length && <p className="people-empty">No people match these filters.</p>}
            </div>
            <p className="people-index-foot">{rebuilding ? "Recovering names from earlier scenes…" : "Only known connections are drawn."}</p>
          </aside>
          <main className="people-graph-main">
            <div className="people-graph-tools"><label htmlFor="relationship-filter">Show connections</label>
              <select id="relationship-filter" value={relationshipFilter} onChange={(event) => setRelationshipFilter(event.target.value as RelationshipFilter)}>
                <option value="all">All types</option><option value="kinship">Family</option>
                <option value="trust">Trust</option><option value="respect">Respect</option>
                <option value="fear">Fear</option><option value="hostility">Hostility</option>
                {presentAxes.map((axis) => <option key={axis} value={axis}>{AXIS_LABELS[axis]}</option>)}
              </select>
              {backgroundCount > 0 && <label className="background-toggle"><input type="checkbox" checked={showBackground} onChange={(event) => setShowBackground(event.target.checked)} />Background ({backgroundCount})</label>}
            </div>
            <RelationshipGraph people={graphPeople} relationships={relationships} protagonistName={campaign.protagonist_name}
              selectedId={focused ? selected?.id ?? null : null} selectedEdgeId={selectedEdgeId} importanceOf={importanceOf}
              edgeStyle={edgeStyle} onSelectPerson={choosePerson} onSelectEdge={setSelectedEdgeId} layoutKey={layoutKey}
              reduceMotion={Boolean(reduceMotion)} />
            <p className="people-graph-note">{graphPeople.length} people · {relationships.length} connections{!showBackground && backgroundCount ? ` · ${backgroundCount} background hidden` : ""}. Drag anyone and their connections follow · hover to trace a web · pinch or ⌘-scroll to zoom · click a line for its history.</p>
          </main>
          <aside className="people-inspector" aria-label="Selected person details">
            <AnimatePresence mode="wait" initial={false}>
              <motion.div key={selectedEdge ? `edge-${selectedEdge.id}` : `person-${selected?.id ?? "none"}`}
                initial={{ opacity: 0, x: reduceMotion ? 0 : 10 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: reduceMotion ? 0 : -10 }}
                transition={{ duration: 0.18 }}>
                {edgeDetail || detail || <p className="people-empty">No one has entered the record yet.</p>}
              </motion.div>
            </AnimatePresence>
          </aside>
        </div>
      </section>
    </div>, document.body)}
  </div>;
}
