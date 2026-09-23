"use client";

import Image from "next/image";
import { useQuery } from "@tanstack/react-query";
import { FormEvent, PointerEvent, WheelEvent, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AtlasArtwork } from "@/components/AtlasArtwork";
import { api, portraitUrl } from "@/lib/api";
import type { CampaignDetail, Character, Relationship } from "@/lib/api";

type Props = {
  campaign: CampaignDetail;
  onReindex: () => void;
  rebuilding: boolean;
  rebuilt: boolean;
  onRefresh: () => void;
};

type PeopleFilter = "all" | "allies" | "enemies" | "factions";
type RelationshipFilter = "all" | "kinship" | "trust" | "respect" | "fear" | "hostility";
type Axis = "trust" | "respect" | "fear" | "hostility";
type ProfileDraft = { role: string; personality: string; appearance: string; sex: string; gender: string; pronouns: string };
type RelationDraft = Record<Axis, string> & { status: string; summary: string };

const MIN_WIDTH = 700;
const MIN_HEIGHT = 560;
const AXES: Axis[] = ["trust", "respect", "fear", "hostility"];
const AXIS_LABELS: Record<Axis, string> = { trust: "Trust", respect: "Respect", fear: "Fear", hostility: "Hostility" };

function isIndividual(person: Character, protagonistName: string) {
  return person.name === protagonistName || !/\b(?:guards|priests|soldiers|villagers|citizens)\b/i.test(person.name);
}

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
  return Array.isArray(history) ? history.filter((entry) => entry && typeof entry === "object" && typeof entry.reason === "string") : [];
}

function displayRelationType(relation: Relationship) {
  const kind = relation.dimensions?.kinship;
  if (kind) return String(kind).replaceAll("_", " ");
  const axis = edgeAxis(relation);
  return axis ? AXIS_LABELS[axis] : relation.summary || "Known";
}

function factsFor(person: Character) {
  const facts = person.attributes?.known_facts;
  return Array.isArray(facts) ? facts.filter((fact): fact is string => typeof fact === "string" && fact.trim().length > 0) : [];
}

export function PeoplePanel({ campaign, onReindex, rebuilding, rebuilt, onRefresh }: Props) {
  const [view, setView] = useState<"list" | "graph">("list");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [peopleFilter, setPeopleFilter] = useState<PeopleFilter>("all");
  const [relationshipFilter, setRelationshipFilter] = useState<RelationshipFilter>("all");
  const [search, setSearch] = useState("");
  const [zoom, setZoom] = useState(1);
  const [avatarJob, setAvatarJob] = useState<{ characterId: string; id: string } | null>(null);
  const [avatarError, setAvatarError] = useState("");
  const [requestingAvatar, setRequestingAvatar] = useState(false);
  const avatarInputRef = useRef<HTMLInputElement>(null);
  const imageSettings = useQuery({ queryKey: ["image-settings"], queryFn: api.imageSettings, retry: false });
  const [editing, setEditing] = useState<"profile" | "relationship" | null>(null);
  const [saving, setSaving] = useState(false);
  const [profileDraft, setProfileDraft] = useState<ProfileDraft>({ role: "", personality: "", appearance: "", sex: "", gender: "", pronouns: "" });
  const [relationDraft, setRelationDraft] = useState<RelationDraft>({ trust: "", respect: "", fear: "", hostility: "", status: "", summary: "" });
  const graphScrollRef = useRef<HTMLDivElement>(null);
  const panRef = useRef<{ pointerId: number; x: number; y: number; left: number; top: number } | null>(null);
  const choosePerson = (id: string) => { setSelectedId(id); setEditing(null); setAvatarError(""); };

  const protagonist = campaign.characters.find((person) => person.name === campaign.protagonist_name);
  const people = campaign.characters.filter((person) => person.name !== campaign.protagonist_name && isIndividual(person, campaign.protagonist_name));
  const matchingPeople = people.filter((person) => {
    const category = categoryFor(person, campaign.protagonist_name, campaign.relationships);
    const faction = person.attributes?.faction ?? person.attributes?.faction_name;
    const inFaction = isFactionMember(person);
    const matchesFilter = peopleFilter === "all" || (peopleFilter === "factions" ? inFaction : category === peopleFilter);
    const query = search.trim().toLocaleLowerCase();
    const matchesSearch = !query || `${person.name} ${person.role} ${String(faction ?? "")}`.toLocaleLowerCase().includes(query);
    return matchesFilter && matchesSearch;
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
  const selectedRelation = socialRelation(selected, campaign.protagonist_name, campaign.relationships);
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
        const axis = (key: Axis) => relationDraft[key] === "" ? null : Number(relationDraft[key]);
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

  const otherGraphPeople = graphPeople.filter((person) => person.name !== campaign.protagonist_name);
  const rings: Character[][] = [];
  let nextPerson = 0;
  for (let ring = 0; nextPerson < otherGraphPeople.length; ring += 1) {
    const capacity = 8 + ring * 6;
    rings.push(otherGraphPeople.slice(nextPerson, nextPerson + capacity));
    nextPerson += capacity;
  }
  const outerRadius = rings.length ? 215 + (rings.length - 1) * 185 : 215;
  const graphWidth = Math.max(MIN_WIDTH, (outerRadius + 130) * 2);
  const graphHeight = Math.max(MIN_HEIGHT, (outerRadius + 115) * 2);
  const positions = new Map<string, { x: number; y: number }>();
  if (protagonist && graphPeople.some((person) => person.name === campaign.protagonist_name)) {
    positions.set(protagonist.name, { x: graphWidth / 2, y: graphHeight / 2 });
  }
  rings.forEach((peopleOnRing, ring) => {
    const radius = 215 + ring * 185;
    peopleOnRing.forEach((person, index) => {
      const angle = -Math.PI / 2 + index * (2 * Math.PI / peopleOnRing.length) + ring * 0.12;
      positions.set(person.name, { x: graphWidth / 2 + Math.cos(angle) * radius, y: graphHeight / 2 + Math.sin(angle) * radius });
    });
  });

  useEffect(() => {
    if (view !== "graph") return;
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") setView("list"); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [view]);

  useEffect(() => {
    if (view !== "graph") return;
    const scroll = graphScrollRef.current;
    if (!scroll) return;
    scroll.scrollLeft = Math.max(0, (scroll.scrollWidth - scroll.clientWidth) / 2);
    scroll.scrollTop = Math.max(0, (scroll.scrollHeight - scroll.clientHeight) / 2);
  }, [view, graphWidth, graphHeight]);

  const zoomAt = (next: number, clientX?: number, clientY?: number) => {
    const scroll = graphScrollRef.current;
    const clamped = Math.max(0.45, Math.min(1.8, Number(next.toFixed(3))));
    if (!scroll || clamped === zoom) return;
    const rect = scroll.getBoundingClientRect();
    const x = clientX === undefined ? rect.width / 2 : clientX - rect.left;
    const y = clientY === undefined ? rect.height / 2 : clientY - rect.top;
    const contentX = (scroll.scrollLeft + x) / zoom;
    const contentY = (scroll.scrollTop + y) / zoom;
    setZoom(clamped);
    requestAnimationFrame(() => {
      scroll.scrollLeft = contentX * clamped - x;
      scroll.scrollTop = contentY * clamped - y;
    });
  };

  const handleGraphWheel = (event: WheelEvent<HTMLDivElement>) => {
    const mouseWheel = event.deltaMode !== 0 || (Math.abs(event.deltaY) >= 50 && Math.abs(event.deltaX) < 2);
    if (event.ctrlKey || event.metaKey || mouseWheel) {
      event.preventDefault();
      const amount = event.ctrlKey || event.metaKey
        ? Math.max(-0.18, Math.min(0.18, event.deltaY * 0.004))
        : Math.sign(event.deltaY) * 0.14;
      zoomAt(zoom * Math.exp(-amount), event.clientX, event.clientY);
    }
    // Two-finger trackpad scrolling retains native horizontal and vertical panning.
  };

  const startPan = (event: PointerEvent<HTMLDivElement>) => {
    const scroll = graphScrollRef.current;
    if (!scroll || event.pointerType === "touch" || (event.button !== 0 && event.button !== 1)) return;
    if (event.button === 0 && (event.target as Element).closest("button, input, select, a")) return;
    panRef.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY, left: scroll.scrollLeft, top: scroll.scrollTop };
    scroll.setPointerCapture(event.pointerId);
    scroll.classList.add("is-panning");
  };

  const movePan = (event: PointerEvent<HTMLDivElement>) => {
    const pan = panRef.current;
    const scroll = graphScrollRef.current;
    if (!pan || !scroll || pan.pointerId !== event.pointerId) return;
    scroll.scrollLeft = pan.left - (event.clientX - pan.x);
    scroll.scrollTop = pan.top - (event.clientY - pan.y);
  };

  const stopPan = (event: PointerEvent<HTMLDivElement>) => {
    const scroll = graphScrollRef.current;
    if (!scroll || panRef.current?.pointerId !== event.pointerId) return;
    panRef.current = null;
    scroll.classList.remove("is-panning");
    if (scroll.hasPointerCapture(event.pointerId)) scroll.releasePointerCapture(event.pointerId);
  };

  const detail = selected && <section className="person-detail" aria-live="polite">
    <div className="person-detail-head"><div className="person-identity">
      <div className="person-portrait" aria-label={avatarUrl ? `Portrait of ${selected.name}` : `No portrait for ${selected.name}`}>
        {avatarUrl ? <Image src={avatarUrl} alt="" width={68} height={68} unoptimized /> : <span aria-hidden="true">{selected.name.charAt(0).toLocaleUpperCase()}</span>}
      </div><div><p className="person-detail-role">{selected.role || "Role unknown"}</p><h3>{selected.name}</h3></div></div>
      {selected.status && selected.status !== "alive" && <span className="person-status">{selected.status}</span>}</div>
    <div className="portrait-controls">
      {(imageProvider === "comfyui" || imageProvider === "ai_horde") && <button type="button" className="portrait-action" onClick={() => void generateAvatar()} disabled={requestingAvatar || Boolean(pendingAvatarJob)}>{requestingAvatar ? "Requesting…" : pendingAvatarJob ? "Portrait generating…" : avatarUrl ? "Regenerate portrait" : "Generate portrait"}</button>}
      {avatarUrl && (imageProvider === "comfyui" || imageProvider === "ai_horde") && <button type="button" className="portrait-action" onClick={() => void generateAvatar(true)} disabled={requestingAvatar || Boolean(pendingAvatarJob)}>New seed</button>}
      <button type="button" className="portrait-action" onClick={() => avatarInputRef.current?.click()} disabled={requestingAvatar}>Change / upload</button>
      {avatarUrl && <button type="button" className="portrait-action" onClick={() => void removeAvatar()} disabled={requestingAvatar}>Remove</button>}
      <input ref={avatarInputRef} type="file" accept="image/png,image/jpeg,image/webp" className="visually-hidden" aria-label={`Upload portrait for ${selected.name}`} onChange={(event) => void uploadAvatar(event.target.files?.[0])} />
    </div>
    <p className="portrait-note">{imageProvider === "comfyui" ? "Queued on your configured ComfyUI server. The story continues while it generates." : imageProvider === "ai_horde" ? "Sends character appearance to AI Horde. The image is copied into Boundless storage." : imageProvider === "perchance_assisted" ? "Generate in Perchance, then upload the saved image here." : "Enable a portrait provider in Settings, or upload an image directly."}</p>
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
    <dl>{([
      ["Gender", selected.attributes?.gender], ["Pronouns", selected.attributes?.pronouns],
      ["First met", selected.attributes?.first_meeting_place], ["Faction", selected.attributes?.faction ?? selected.attributes?.faction_name],
    ] as Array<[string, unknown]>).filter(([, value]) => typeof value === "string" && value.trim()).map(([label, value]) =>
      <div key={label}><dt>{label}</dt><dd>{String(value)}</dd></div>)}</dl>
    {selected.personality && <p>{selected.personality}</p>}
    {selected.motivations?.length > 0 && <div className="person-facts"><h4>Motives</h4><ul>{selected.motivations.map((motive, index) => <li key={`${motive}-${index}`}>{motive}</li>)}</ul></div>}
    {knownFacts.length > 0 && <div className="person-facts"><h4>Known facts</h4><ul>{knownFacts.map((fact, index) => <li key={`${fact}-${index}`}>{fact}</li>)}</ul></div>}
    <div className="relationship-readout">
      <div className="relationship-headline"><h4>Relationship with {campaign.protagonist_name}</h4>{selectedRelation && <button type="button" onClick={editRelationship}>Edit</button>}</div>
      {editing === "relationship" && <form className="person-edit-form" onSubmit={(event) => void saveEdit(event)}>
        <div className="person-edit-grid">{AXES.map((axis) => <label key={axis}>{AXIS_LABELS[axis]}<input type="number" min={0} max={100} placeholder="Unknown" value={relationDraft[axis]} onChange={(event) => setRelationDraft({ ...relationDraft, [axis]: event.target.value })} /></label>)}</div>
        <label>Status<select value={relationDraft.status} onChange={(event) => setRelationDraft({ ...relationDraft, status: event.target.value })}><option value="">Derived from scores</option><option value="Known acquaintance">Known acquaintance</option><option value="Trusted ally">Trusted ally</option><option value="Tense relationship">Tense relationship</option><option value="Hostile">Hostile</option><option value="Family">Family</option><option value="Stranger">Stranger</option></select></label>
        <label>Relationship note<textarea rows={2} maxLength={2000} value={relationDraft.summary} onChange={(event) => setRelationDraft({ ...relationDraft, summary: event.target.value })} /></label>
        <div className="person-edit-actions"><button type="button" onClick={() => setEditing(null)}>Cancel</button><button type="submit" disabled={saving}>{saving ? "Saving…" : "Save relationship"}</button></div>
      </form>}
      <p className="relationship-direction">{selectedRelation ? `${selectedRelation.from} → ${selectedRelation.to}` : "No relationship scores recorded"}</p>
      <div className="relationship-metrics">{AXES.map((axis) => {
        const value = score(selectedRelation, axis);
        return <div className="relationship-metric" key={axis} data-axis={axis}>
          <span>{AXIS_LABELS[axis]}</span><strong>{value === null ? "—" : value}</strong>
          <div className="relationship-meter" role="img" aria-label={`${AXIS_LABELS[axis]} ${value === null ? "not recorded" : `${value} out of 100`}`}>
            {value !== null && <span style={{ width: `${value}%` }} />}
          </div>
        </div>;
      })}</div>
      <div className="relationship-status"><span>Status</span><strong>{relationshipStatus(selectedRelation)}</strong></div>
      <div className="person-facts"><h4>Why</h4>
        {historyFor(selectedRelation).length ? <ul>{historyFor(selectedRelation).map((entry, index) =>
          <li key={`${entry.turn_id ?? entry.turn_index ?? "turn"}-${index}`}>{entry.reason}{typeof entry.location === "string" ? ` · ${entry.location}` : ""}</li>)}</ul>
          : <p className="empty-relationship-note">No interaction notes have been recorded.</p>}
      </div>
      {lastInteraction && typeof lastInteraction === "object" && <div className="person-last-interaction">
        <span>Last interaction</span>
        <strong>Turn {String(lastInteraction.turn_index ?? "—")} · {recordLocation(selectedRelation) || "Location unknown"}</strong>
      </div>}
    </div>
    {connections.length > 0 && <div className="person-facts"><h4>Other connections</h4><ul>{connections.filter((relation) => relation.id !== selectedRelation?.id).map((relation) => <li key={relation.id}>{connectionLabel(relation, selected.name)}</li>)}</ul></div>}
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
              </select>
              <span className="people-graph-spacer" />
              <button type="button" aria-label="Zoom out" disabled={zoom <= 0.45} onClick={() => zoomAt(zoom - 0.15)}>−</button>
              <span aria-live="polite">{Math.round(zoom * 100)}%</span>
              <button type="button" aria-label="Zoom in" disabled={zoom >= 1.8} onClick={() => zoomAt(zoom + 0.15)}>+</button>
              <button type="button" onClick={() => { const scroll = graphScrollRef.current; if (scroll) zoomAt(Math.max(0.45, Math.min(1, scroll.clientWidth / graphWidth, scroll.clientHeight / graphHeight))); }}>Fit</button>
              <button type="button" onClick={() => zoomAt(1)}>Reset</button>
            </div>
            <div className="people-graph-scroll" ref={graphScrollRef} onWheel={handleGraphWheel} onPointerDown={startPan} onPointerMove={movePan} onPointerUp={stopPan} onPointerCancel={stopPan}><div className="people-graph-stage" style={{ width: graphWidth * zoom, height: graphHeight * zoom }}><div className="people-graph-scale" style={{ width: graphWidth, height: graphHeight, transform: `scale(${zoom})` }}>
              <div className="people-graph" role="group" aria-label={`Character relationship graph with ${graphPeople.length} people and ${relationships.length} connections`} style={{ width: graphWidth, height: graphHeight }}>
                <div className="people-map-art" aria-hidden="true"><AtlasArtwork compact /></div>
                <svg viewBox={`0 0 ${graphWidth} ${graphHeight}`} aria-hidden="true">
                  {relationships.map((relation) => {
                    const from = positions.get(relation.from); const to = positions.get(relation.to);
                    if (!from || !to) return null;
                    const active = selected?.name === relation.from || selected?.name === relation.to;
                    const axis = edgeAxis(relation);
                    const label = axis ? `${AXIS_LABELS[axis]} ${score(relation, axis)}`
                      : relation.dimensions?.kinship || relation.dimensions?.awareness ? "" : displayRelationType(relation);
                    const midpointX = (from.x + to.x) / 2;
                    const midpointY = (from.y + to.y) / 2;
                    const curve = from.y <= to.y ? -18 : 18;
                    return <g key={relation.id} className={`people-edge-group${active ? " is-active" : ""}`} data-axis={axis ?? "known"}>
                      <path d={`M ${from.x} ${from.y} Q ${midpointX} ${midpointY + curve} ${to.x} ${to.y}`} />
                      {label && <text x={midpointX} y={midpointY + curve - 4}>{label}</text>}
                    </g>;
                  })}
                </svg>
                {graphPeople.map((person) => {
                  const position = positions.get(person.name);
                  if (!position) return null;
                  return <button type="button" key={person.id} className="people-node"
                    data-player={person.name === campaign.protagonist_name} aria-pressed={selected?.id === person.id}
                    style={{ left: position.x, top: position.y }} onClick={() => choosePerson(person.id)}>
                    <span className="people-node-portrait" aria-hidden="true">{portraitUrl(person.attributes?.avatar_url) ? <Image src={portraitUrl(person.attributes?.avatar_url)} alt="" width={46} height={46} unoptimized /> : person.name.charAt(0).toLocaleUpperCase()}</span>
                    <strong>{person.name}</strong><span>{person.role || (person.name === campaign.protagonist_name ? "Player character" : "Role unknown")}</span>
                  </button>;
                })}
              </div>
            </div></div></div>
            <p className="people-graph-note">All {graphPeople.length} visible people · {relationships.length} connections. Mouse wheel or pinch to zoom. Two-finger scroll or drag the canvas to move.</p>
          </main>
          <aside className="people-inspector" aria-label="Selected person details">
            {detail ?? <p className="people-empty">No one has entered the record yet.</p>}
          </aside>
        </div>
      </section>
    </div>, document.body)}
  </div>;
}
