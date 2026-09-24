"use client";

import Image from "next/image";
import { useQuery } from "@tanstack/react-query";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { FormEvent, PointerEvent, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { faceCrop, PortraitButton } from "@/components/PortraitLightbox";
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

const MIN_WIDTH = 700;
const MIN_HEIGHT = 560;
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
  const [zoom, setZoom] = useState(1);
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
  const graphScrollRef = useRef<HTMLDivElement>(null);
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
  const connectedNames = new Set(focused && selected ? [selected.name, ...connections.flatMap((relation) => [relation.from, relation.to])] : []);
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

  const degree = (person: Character) => relationships.filter((relation) => relation.from === person.name || relation.to === person.name).length;
  const otherGraphPeople = graphPeople.filter((person) => person.name !== campaign.protagonist_name)
    .sort((left, right) => IMPORTANCE_ORDER.indexOf(importanceOf(left)) - IMPORTANCE_ORDER.indexOf(importanceOf(right)) || degree(right) - degree(left));
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

  // ── Camera: a transform (pan + zoom) over a fixed-size world, driven by native listeners
  // so trackpad pinch and two-finger pans never fall through to the page.
  const layoutKey = `boundless:graph-layout:${campaign.id}:${campaign.branch.id}`;
  const [overrides, setOverrides] = useState<Record<string, { x: number; y: number }>>({});
  useEffect(() => {
    try {
      const saved = window.localStorage.getItem(layoutKey);
      const parsed = saved ? JSON.parse(saved) : {};
      const timer = window.setTimeout(() => setOverrides(parsed && typeof parsed === "object" ? parsed : {}), 0);
      return () => window.clearTimeout(timer);
    } catch { return undefined; }
  }, [layoutKey]);
  graphPeople.forEach((person) => {
    const moved = overrides[person.id];
    if (moved && positions.has(person.name)) positions.set(person.name, moved);
  });
  const cameraRef = useRef({ x: 0, y: 0, k: 1 });
  const frameRef = useRef(0);
  const applyCamera = () => {
    cancelAnimationFrame(frameRef.current);
    frameRef.current = requestAnimationFrame(() => {
      const { x, y, k } = cameraRef.current;
      const viewport = graphScrollRef.current;
      const world = viewport?.querySelector<HTMLDivElement>(".people-graph-world");
      if (world) world.style.transform = `translate3d(${x}px, ${y}px, 0) scale(${k})`;
      if (viewport) {
        viewport.style.setProperty("--art-size", `${Math.round(2600 * k)}px`);
        viewport.style.setProperty("--art-x", `${Math.round(x * 0.35)}px`);
        viewport.style.setProperty("--art-y", `${Math.round(y * 0.35)}px`);
      }
      setZoom((current) => Math.abs(current - k) > 0.004 ? k : current);
    });
  };
  const zoomAt = (next: number, clientX?: number, clientY?: number) => {
    const viewport = graphScrollRef.current;
    if (!viewport) return;
    const camera = cameraRef.current;
    const k = Math.max(0.3, Math.min(2.4, next));
    const rect = viewport.getBoundingClientRect();
    const px = clientX === undefined ? rect.width / 2 : clientX - rect.left;
    const py = clientY === undefined ? rect.height / 2 : clientY - rect.top;
    camera.x = px - ((px - camera.x) / camera.k) * k;
    camera.y = py - ((py - camera.y) / camera.k) * k;
    camera.k = k;
    applyCamera();
  };
  const fitView = (animate = true) => {
    const viewport = graphScrollRef.current;
    if (!viewport) return;
    const points = [...positions.values()];
    if (!points.length) return;
    const minX = Math.min(...points.map((point) => point.x)) - 110, maxX = Math.max(...points.map((point) => point.x)) + 110;
    const minY = Math.min(...points.map((point) => point.y)) - 90, maxY = Math.max(...points.map((point) => point.y)) + 90;
    const rect = viewport.getBoundingClientRect();
    const k = Math.max(0.3, Math.min(1.2, rect.width / (maxX - minX), rect.height / (maxY - minY)));
    const world = viewport.querySelector<HTMLDivElement>(".people-graph-world");
    if (world) world.classList.toggle("is-gliding", animate && !reduceMotion);
    cameraRef.current = { k, x: rect.width / 2 - ((minX + maxX) / 2) * k, y: rect.height / 2 - ((minY + maxY) / 2) * k };
    applyCamera();
    if (world) window.setTimeout(() => world.classList.remove("is-gliding"), 420);
  };
  useEffect(() => {
    if (view !== "graph") return;
    const timer = window.setTimeout(() => fitView(false), 30);
    return () => window.clearTimeout(timer);
    // Fit once when the graph opens; later layout changes keep the player's camera.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view]);
  useEffect(() => {
    const viewport = graphScrollRef.current;
    if (view !== "graph" || !viewport) return;
    const onWheel = (event: globalThis.WheelEvent) => {
      event.preventDefault();
      const camera = cameraRef.current;
      const pixelDelta = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? 400 : 1;
      const mouseWheel = event.deltaMode !== 0 || (Math.abs(event.deltaY) >= 40 && event.deltaX === 0 && Number.isInteger(event.deltaY));
      if (event.ctrlKey || event.metaKey) {
        zoomAt(camera.k * Math.exp(-event.deltaY * pixelDelta * 0.0105), event.clientX, event.clientY);
      } else if (mouseWheel) {
        zoomAt(camera.k * Math.exp(-Math.sign(event.deltaY) * 0.16), event.clientX, event.clientY);
      } else {
        camera.x -= event.deltaX * pixelDelta;
        camera.y -= event.deltaY * pixelDelta;
        applyCamera();
      }
    };
    // Safari trackpad pinch arrives as gesture events rather than ctrl+wheel.
    let gestureStart = 1;
    const onGestureStart = (event: Event) => { event.preventDefault(); gestureStart = cameraRef.current.k; };
    const onGestureChange = (event: Event) => {
      event.preventDefault();
      const gesture = event as Event & { scale: number; clientX: number; clientY: number };
      zoomAt(gestureStart * gesture.scale, gesture.clientX, gesture.clientY);
    };
    viewport.addEventListener("wheel", onWheel, { passive: false });
    viewport.addEventListener("gesturestart", onGestureStart);
    viewport.addEventListener("gesturechange", onGestureChange);
    return () => {
      viewport.removeEventListener("wheel", onWheel);
      viewport.removeEventListener("gesturestart", onGestureStart);
      viewport.removeEventListener("gesturechange", onGestureChange);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view]);

  const dragRef = useRef<{ pointerId: number; x: number; y: number; nodeId?: string; startX: number; startY: number; moved: boolean } | null>(null);
  const suppressClickRef = useRef(false);
  const startPan = (event: PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 && event.button !== 1) return;
    const node = (event.target as Element).closest<HTMLElement>("[data-node-id]");
    if (!node && (event.target as Element).closest("button, input, select, a, path.edge-hit")) return;
    const nodeId = node?.dataset.nodeId;
    const person = nodeId ? graphPeople.find((row) => row.id === nodeId) : undefined;
    const origin = person ? positions.get(person.name) : undefined;
    dragRef.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY, nodeId,
      startX: origin?.x ?? cameraRef.current.x, startY: origin?.y ?? cameraRef.current.y, moved: false };
    graphScrollRef.current?.setPointerCapture(event.pointerId);
  };
  const movePan = (event: PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
    if (!drag.moved && Math.hypot(dx, dy) < 4) return;
    drag.moved = true;
    graphScrollRef.current?.classList.add(drag.nodeId ? "is-dragging-node" : "is-panning");
    if (drag.nodeId) {
      const k = cameraRef.current.k;
      const nodeId = drag.nodeId;
      setOverrides((current) => ({ ...current, [nodeId]: { x: drag.startX + dx / k, y: drag.startY + dy / k } }));
    } else {
      cameraRef.current.x = drag.startX + dx;
      cameraRef.current.y = drag.startY + dy;
      applyCamera();
    }
  };
  const stopPan = (event: PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    const viewport = graphScrollRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    dragRef.current = null;
    viewport?.classList.remove("is-panning", "is-dragging-node");
    if (viewport?.hasPointerCapture(event.pointerId)) viewport.releasePointerCapture(event.pointerId);
    if (drag.moved) {
      suppressClickRef.current = true;
      window.setTimeout(() => { suppressClickRef.current = false; }, 0);
      if (drag.nodeId) setOverrides((current) => {
        try { window.localStorage.setItem(layoutKey, JSON.stringify(current)); } catch { /* layout is a convenience */ }
        return current;
      });
    }
  };
  const resetLayout = () => {
    setOverrides({});
    try { window.localStorage.removeItem(layoutKey); } catch { /* ignore */ }
    window.setTimeout(() => fitView(), 30);
  };

  const detail = selected && <section className="person-detail" aria-live="polite">
    <div className="person-detail-head"><div className="person-identity">
      {avatarUrl ? <PortraitButton className={`person-portrait${faceCrop(selected.attributes).className}`} style={faceCrop(selected.attributes).style} src={avatarUrl} name={selected.name} caption={selected.role}>
        <Image src={avatarUrl} alt="" width={68} height={68} unoptimized /></PortraitButton>
        : <div className="person-portrait" aria-label={`No portrait for ${selected.name}`}><span aria-hidden="true">{selected.name.charAt(0).toLocaleUpperCase()}</span></div>}<div><p className="person-detail-role">{selected.role || "Role unknown"}</p><h3>{selected.name}</h3>
        {selected.name !== campaign.protagonist_name && <span className={`importance-badge importance-badge--${importanceOf(selected).toLowerCase()}`}>{IMPORTANCE_LABEL[importanceOf(selected)]}</span>}</div></div>
      {selected.status && selected.status !== "alive" && <span className="person-status">{selected.status}</span>}</div>
    <div className="portrait-controls">
      {(imageProvider === "comfyui" || imageProvider === "ai_horde") && <button type="button" className="portrait-action" onClick={() => void generateAvatar()} disabled={requestingAvatar || Boolean(pendingAvatarJob)}>{requestingAvatar ? "Requesting…" : pendingAvatarJob ? "Portrait generating…" : avatarUrl ? "Regenerate portrait" : "Generate portrait"}</button>}
      {avatarUrl && (imageProvider === "comfyui" || imageProvider === "ai_horde") && <button type="button" className="portrait-action" onClick={() => void generateAvatar(true)} disabled={requestingAvatar || Boolean(pendingAvatarJob)}>New seed</button>}
      <button type="button" className="portrait-action" onClick={() => avatarInputRef.current?.click()} disabled={requestingAvatar}>Change / upload</button>
      {avatarUrl && <button type="button" className="portrait-action" onClick={() => void removeAvatar()} disabled={requestingAvatar}>Remove</button>}
      <input ref={avatarInputRef} type="file" accept="image/png,image/jpeg,image/webp" className="visually-hidden" aria-label={`Upload portrait for ${selected.name}`} onChange={(event) => void uploadAvatar(event.target.files?.[0])} />
    </div>
    <p className="portrait-note">{pendingAvatarJob ? "Portrait queued. The story continues while it generates." : imageProvider === "comfyui" ? avatarUrl ? "Portrait saved in Boundless. Regenerate it or choose a new seed at any time." : "Generate a portrait on your configured ComfyUI server." : imageProvider === "ai_horde" ? "Sends character appearance to AI Horde. The image is copied into Boundless storage." : imageProvider === "perchance_assisted" ? "Generate in Perchance, then upload the saved image here." : "Enable a portrait provider in Settings, or upload an image directly."}</p>
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
              <span className="people-graph-spacer" />
              <button type="button" aria-label="Zoom out" disabled={zoom <= 0.3} onClick={() => zoomAt(cameraRef.current.k / 1.2)}>−</button>
              <span aria-live="polite">{Math.round(zoom * 100)}%</span>
              <button type="button" aria-label="Zoom in" disabled={zoom >= 2.4} onClick={() => zoomAt(cameraRef.current.k * 1.2)}>+</button>
              <button type="button" onClick={() => fitView()}>Fit</button>
              {Object.keys(overrides).length > 0 && <button type="button" onClick={resetLayout}>Reset layout</button>}
            </div>
            <div className="people-graph-viewport" ref={graphScrollRef} onPointerDown={startPan} onPointerMove={movePan} onPointerUp={stopPan} onPointerCancel={stopPan}
              onClickCapture={(event) => { if (suppressClickRef.current) { event.stopPropagation(); event.preventDefault(); } }}>
              <div className="people-graph-world" style={{ width: graphWidth, height: graphHeight }}>
              <div className="people-graph" role="group" aria-label={`Character relationship graph with ${graphPeople.length} people and ${relationships.length} connections`} style={{ width: graphWidth, height: graphHeight }}>
                <svg viewBox={`0 0 ${graphWidth} ${graphHeight}`} aria-hidden="true">
                  <defs><marker id="edge-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="currentColor" /></marker></defs>
                  {relationships.map((relation, edgeIndex) => {
                    const from = positions.get(relation.from); const to = positions.get(relation.to);
                    if (!from || !to) return null;
                    const active = selected?.name === relation.from || selected?.name === relation.to || selectedEdgeId === relation.id;
                    const dim = focused && !active;
                    const axis = relationshipFilter !== "all" && relationshipFilter !== "kinship" ? relationshipFilter : edgeAxis(relation);
                    const label = axis && score(relation, axis) !== null ? `${AXIS_LABELS[axis]} ${score(relation, axis)}`
                      : relation.dimensions?.kinship || relation.dimensions?.awareness ? "" : displayRelationType(relation);
                    const midpointX = (from.x + to.x) / 2;
                    const midpointY = (from.y + to.y) / 2;
                    const curve = from.y <= to.y ? -18 : 18;
                    // Stop the arrow at the node's edge so direction stays readable.
                    const length = Math.hypot(to.x - from.x, to.y - from.y) || 1;
                    const endX = to.x - ((to.x - from.x) / length) * 70;
                    const endY = to.y - ((to.y - from.y) / length) * 56;
                    const d = `M ${from.x} ${from.y} Q ${midpointX} ${midpointY + curve} ${endX} ${endY}`;
                    const width = axis ? 1.2 + ((score(relation, axis) ?? 0) / 100) * 2.4 : 1.4;
                    return <g key={relation.id} className={`people-edge-group${active ? " is-active" : ""}${dim ? " is-dim" : ""}${selectedEdgeId === relation.id ? " is-selected" : ""}`} data-axis={axis ?? "known"}>
                      <motion.path d={d} markerEnd="url(#edge-arrow)" style={{ strokeWidth: width }}
                        initial={reduceMotion ? false : { pathLength: 0, opacity: 0 }} animate={{ pathLength: 1, opacity: 1 }}
                        transition={{ duration: 0.7, delay: Math.min(edgeIndex * 0.03, 0.6), ease: "easeOut" }} />
                      <path d={d} className="edge-hit" onClick={() => { setSelectedEdgeId(relation.id); }} />
                      {label && <text x={midpointX} y={midpointY + curve - 4}>{label}</text>}
                    </g>;
                  })}
                </svg>
                {graphPeople.map((person, nodeIndex) => {
                  const position = positions.get(person.name);
                  if (!position) return null;
                  const dimmed = focused && !connectedNames.has(person.name) && person.name !== campaign.protagonist_name;
                  return <motion.button type="button" key={person.id} data-node-id={person.id} className={`people-node${dimmed ? " is-dim" : ""}${overrides[person.id] ? " is-placed" : ""}`}
                    data-player={person.name === campaign.protagonist_name} data-importance={importanceOf(person).toLowerCase()} aria-pressed={selected?.id === person.id}
                    style={{ left: position.x, top: position.y, x: "-50%", y: "-50%" }} onClick={() => choosePerson(person.id)}
                    initial={reduceMotion ? false : { opacity: 0, scale: 0.8 }} animate={{ opacity: dimmed ? 0.42 : 1, scale: 1 }}
                    whileHover={reduceMotion ? undefined : { scale: 1.04 }}
                    transition={{ type: "spring", stiffness: 320, damping: 26, delay: reduceMotion ? 0 : Math.min(nodeIndex * 0.025, 0.5) }}>
                    <span className={`people-node-portrait${faceCrop(person.attributes).className}`} style={faceCrop(person.attributes).style} aria-hidden="true">{portraitUrl(person.attributes?.avatar_url) ? <Image src={portraitUrl(person.attributes?.avatar_url)} alt="" width={46} height={46} unoptimized /> : person.name.charAt(0).toLocaleUpperCase()}</span>
                    <strong>{person.name}</strong><span>{person.role || (person.name === campaign.protagonist_name ? "Player character" : "Role unknown")}</span>
                  </motion.button>;
                })}
              </div>
            </div></div>
            <p className="people-graph-note">{graphPeople.length} people · {relationships.length} connections{!showBackground && backgroundCount ? ` · ${backgroundCount} background hidden` : ""}. Drag people to arrange them · pinch or ⌘-scroll to zoom · two-finger scroll to move · click a line for its history.</p>
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
