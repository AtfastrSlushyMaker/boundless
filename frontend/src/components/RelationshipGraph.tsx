"use client";

import Image from "next/image";
import { Maximize2, Minus, Plus, Shuffle } from "lucide-react";
import { PointerEvent, useEffect, useMemo, useRef, useState } from "react";
import { Spot } from "@/components/Art";
import { faceCrop } from "@/components/PortraitLightbox";
import { portraitUrl } from "@/lib/api";
import type { Character, Importance, Relationship } from "@/lib/api";
import { createSim, isActive, reheat, seedPosition, Sim, SimNode, tick } from "@/lib/forceSim";

export type EdgeStyle = { axis: string | null; label: string; width: number };

type Props = {
  people: Character[];
  relationships: Relationship[];
  protagonistName: string;
  selectedId: string | null;
  selectedEdgeId: string | null;
  importanceOf: (person: Character) => Importance;
  edgeStyle: (relation: Relationship) => EdgeStyle;
  onSelectPerson: (id: string) => void;
  onSelectEdge: (id: string) => void;
  layoutKey: string;
  reduceMotion: boolean;
  filtered?: boolean;
};

const RADIUS: Record<Importance, number> = { COMPANION: 27, MAJOR: 25, RECURRING: 21, MINOR: 17, BACKGROUND: 13 };
const MIN_ZOOM = 0.2;
const MAX_ZOOM = 2.6;

type Camera = { x: number; y: number; k: number };

/** A live, force-directed relationship graph: drag people and their connections follow. */
export function RelationshipGraph({ people, relationships, protagonistName, selectedId, selectedEdgeId, importanceOf, edgeStyle,
  onSelectPerson, onSelectEdge, layoutKey, reduceMotion, filtered = false }: Props) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const worldRef = useRef<HTMLDivElement>(null);
  const nodeRefs = useRef(new Map<string, HTMLButtonElement>());
  const edgeRefs = useRef(new Map<string, { line: SVGLineElement | null; hit: SVGLineElement | null; label: SVGTextElement | null }>());
  const simRef = useRef<Sim | null>(null);
  const cameraRef = useRef<Camera>({ x: 0, y: 0, k: 1 });
  const touchedCameraRef = useRef(false);
  const frameRef = useRef(0);
  const [zoom, setZoom] = useState(1);
  const [hoverId, setHoverId] = useState<string | null>(null);

  const nameToId = useMemo(() => new Map(people.map((person) => [person.name, person.id])), [people]);
  const edges = useMemo(() => relationships.flatMap((relation) => {
    const from = nameToId.get(relation.from), to = nameToId.get(relation.to);
    return from && to && from !== to ? [{ relation, from, to }] : [];
  }), [relationships, nameToId]);
  const degree = useMemo(() => {
    const counts = new Map<string, number>();
    edges.forEach(({ from, to }) => { counts.set(from, (counts.get(from) ?? 0) + 1); counts.set(to, (counts.get(to) ?? 0) + 1); });
    return counts;
  }, [edges]);
  const radiusOf = (person: Character) => (person.name === protagonistName ? 32 : RADIUS[importanceOf(person)])
    + Math.min(degree.get(person.id) ?? 0, 6) * 1.5;

  // Neighbourhood to highlight: whoever is hovered, else the selected person.
  const focusId = hoverId ?? selectedId;
  const neighbours = useMemo(() => {
    if (!focusId) return null;
    const set = new Set([focusId]);
    edges.forEach(({ from, to }) => { if (from === focusId) set.add(to); if (to === focusId) set.add(from); });
    return set;
  }, [focusId, edges]);

  // ── Rendering: positions are written straight to the DOM each frame; React only renders structure.
  const draw = () => {
    const sim = simRef.current;
    if (!sim) return;
    const byId = new Map(sim.nodes.map((node) => [node.id, node]));
    for (const node of sim.nodes) {
      const element = nodeRefs.current.get(node.id);
      if (element) element.style.transform = `translate3d(${node.x}px, ${node.y}px, 0)`;
    }
    for (const { relation, from, to } of edges) {
      const refs = edgeRefs.current.get(relation.id);
      const a = byId.get(from), b = byId.get(to);
      if (!refs || !a || !b) continue;
      const dx = b.x - a.x, dy = b.y - a.y;
      const length = Math.hypot(dx, dy) || 1;
      const x1 = a.x + (dx / length) * a.r, y1 = a.y + (dy / length) * a.r;
      const x2 = b.x - (dx / length) * (b.r + 5), y2 = b.y - (dy / length) * (b.r + 5);
      for (const line of [refs.line, refs.hit]) {
        if (!line) continue;
        line.setAttribute("x1", x1.toFixed(1)); line.setAttribute("y1", y1.toFixed(1));
        line.setAttribute("x2", x2.toFixed(1)); line.setAttribute("y2", y2.toFixed(1));
      }
      if (refs.label) { refs.label.setAttribute("x", ((x1 + x2) / 2).toFixed(1)); refs.label.setAttribute("y", ((y1 + y2) / 2 - 5).toFixed(1)); }
    }
  };

  const applyCamera = () => {
    const { x, y, k } = cameraRef.current;
    if (worldRef.current) worldRef.current.style.transform = `translate3d(${x}px, ${y}px, 0) scale(${k})`;
    const viewport = viewportRef.current;
    if (viewport) {
      viewport.style.setProperty("--art-size", `${Math.round(2600 * Math.max(k, 0.45))}px`);
      viewport.style.setProperty("--art-x", `${Math.round(x * 0.35)}px`);
      viewport.style.setProperty("--art-y", `${Math.round(y * 0.35)}px`);
      viewport.classList.toggle("is-far", k < 0.55);
    }
    setZoom((current) => Math.abs(current - k) > 0.004 ? k : current);
  };

  const fitView = (animate = true) => {
    const viewport = viewportRef.current, sim = simRef.current;
    if (!viewport || !sim?.nodes.length) return;
    const rect = viewport.getBoundingClientRect();
    const minX = Math.min(...sim.nodes.map((node) => node.x - node.r)) - 60, maxX = Math.max(...sim.nodes.map((node) => node.x + node.r)) + 60;
    const minY = Math.min(...sim.nodes.map((node) => node.y - node.r)) - 40, maxY = Math.max(...sim.nodes.map((node) => node.y + node.r)) + 70;
    const k = Math.max(MIN_ZOOM, Math.min(1.15, rect.width / (maxX - minX), rect.height / (maxY - minY)));
    worldRef.current?.classList.toggle("is-gliding", animate && !reduceMotion);
    cameraRef.current = { k, x: rect.width / 2 - ((minX + maxX) / 2) * k, y: rect.height / 2 - ((minY + maxY) / 2) * k };
    applyCamera();
    window.setTimeout(() => worldRef.current?.classList.remove("is-gliding"), 450);
  };

  const saveLayout = () => {
    const sim = simRef.current;
    if (!sim) return;
    try {
      window.localStorage.setItem(layoutKey, JSON.stringify(Object.fromEntries(
        sim.nodes.map((node) => [node.id, { x: Math.round(node.x), y: Math.round(node.y) }]))));
    } catch { /* layout memory is a convenience */ }
  };

  const loop = () => {
    cancelAnimationFrame(frameRef.current);
    const step = () => {
      const sim = simRef.current;
      if (!sim) return;
      tick(sim);
      draw();
      if (isActive(sim)) {
        frameRef.current = requestAnimationFrame(step);
      } else {
        saveLayout();
        if (!touchedCameraRef.current) fitView();
      }
    };
    frameRef.current = requestAnimationFrame(step);
  };

  // (Re)build the simulation when the set of people or connections changes, keeping known positions.
  const structureKey = `${people.map((person) => person.id).join(",")}|${edges.map(({ from, to }) => `${from}>${to}`).join(",")}`;
  useEffect(() => {
    let saved: Record<string, { x: number; y: number }> = {};
    try { saved = JSON.parse(window.localStorage.getItem(layoutKey) ?? "{}") ?? {}; } catch { saved = {}; }
    const previous = new Map((simRef.current?.nodes ?? []).map((node) => [node.id, node]));
    const nodes: SimNode[] = [];
    const placed = new Map<string, SimNode>();
    const ordered = [...people].sort((left, right) => (right.name === protagonistName ? 1 : 0) - (left.name === protagonistName ? 1 : 0)
      || (degree.get(right.id) ?? 0) - (degree.get(left.id) ?? 0));
    let fresh = 0;
    ordered.forEach((person, index) => {
      const known = previous.get(person.id) ?? (saved[person.id] ? { ...saved[person.id], vx: 0, vy: 0 } : null);
      let x: number, y: number;
      if (known) { ({ x, y } = known); } else {
        fresh += 1;
        const neighbour = edges.map(({ from, to }) => from === person.id ? placed.get(to) : to === person.id ? placed.get(from) : undefined).find(Boolean);
        ({ x, y } = person.name === protagonistName ? { x: 0, y: 0 } : seedPosition(index, neighbour));
      }
      const node: SimNode = { id: person.id, x, y, vx: 0, vy: 0, r: radiusOf(person) };
      nodes.push(node); placed.set(person.id, node);
    });
    const index = new Map(nodes.map((node, position) => [node.id, position]));
    const links = edges.map(({ from, to }) => ({ source: index.get(from)!, target: index.get(to)! }));
    const firstBuild = !simRef.current;
    simRef.current = createSim(nodes, links, reduceMotion ? 0 : fresh ? (fresh === nodes.length ? 1 : 0.5) : 0.25);
    if (reduceMotion) {
      simRef.current.alpha = 1;
      for (let i = 0; i < 300; i += 1) tick(simRef.current);
      simRef.current.alpha = 0;
    }
    // Draw after React has attached the node elements.
    const timer = window.setTimeout(() => {
      draw();
      if (firstBuild) fitView(false);
      loop();
    }, 0);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [structureKey, layoutKey]);
  // Toggled directly: the viewport also carries drag and zoom classes that React must not reset.
  useEffect(() => { viewportRef.current?.classList.toggle("is-hovering", hoverId !== null); }, [hoverId]);
  // Labels appear with the highlight; place them even when the layout is at rest.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { draw(); }, [neighbours, selectedEdgeId]);
  useEffect(() => () => { cancelAnimationFrame(frameRef.current); saveLayout(); },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    []);

  // ── Camera: native listeners so trackpad pinch and two-finger pans never reach the page.
  const zoomAt = (next: number, clientX?: number, clientY?: number) => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    touchedCameraRef.current = true;
    const camera = cameraRef.current;
    const k = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, next));
    const rect = viewport.getBoundingClientRect();
    const px = clientX === undefined ? rect.width / 2 : clientX - rect.left;
    const py = clientY === undefined ? rect.height / 2 : clientY - rect.top;
    camera.x = px - ((px - camera.x) / camera.k) * k;
    camera.y = py - ((py - camera.y) / camera.k) * k;
    camera.k = k;
    applyCamera();
  };
  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    const onWheel = (event: globalThis.WheelEvent) => {
      event.preventDefault();
      const camera = cameraRef.current;
      const pixelDelta = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? 400 : 1;
      const mouseWheel = event.deltaMode !== 0 || (Math.abs(event.deltaY) >= 40 && event.deltaX === 0 && Number.isInteger(event.deltaY));
      if (event.ctrlKey || event.metaKey) zoomAt(camera.k * Math.exp(-event.deltaY * pixelDelta * 0.0105), event.clientX, event.clientY);
      else if (mouseWheel) zoomAt(camera.k * Math.exp(-Math.sign(event.deltaY) * 0.16), event.clientX, event.clientY);
      else {
        touchedCameraRef.current = true;
        camera.x -= event.deltaX * pixelDelta; camera.y -= event.deltaY * pixelDelta;
        applyCamera();
      }
    };
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
  }, []);

  // ── Pointer: drag a person (the graph reacts live) or drag empty space to pan.
  const dragRef = useRef<{ pointerId: number; x: number; y: number; nodeId?: string; startX: number; startY: number; moved: boolean } | null>(null);
  const suppressClickRef = useRef(false);
  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 && event.button !== 1) return;
    const target = event.target as Element;
    const nodeId = target.closest<HTMLElement>("[data-node-id]")?.dataset.nodeId;
    if (!nodeId && target.closest("button, input, select, a, .graph-edge-hit")) return;
    const node = nodeId ? simRef.current?.nodes.find((row) => row.id === nodeId) : undefined;
    dragRef.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY, nodeId,
      startX: node?.x ?? cameraRef.current.x, startY: node?.y ?? cameraRef.current.y, moved: false };
  };
  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
    if (!drag.moved && Math.hypot(dx, dy) < 4) return;
    const sim = simRef.current;
    if (!drag.moved) {
      drag.moved = true;
      // Capture only once a drag starts, so a plain click still reaches the person under the pointer.
      viewportRef.current?.setPointerCapture(event.pointerId);
      viewportRef.current?.classList.add(drag.nodeId ? "is-dragging-node" : "is-panning");
      if (drag.nodeId && sim) { sim.alphaTarget = 0.25; reheat(sim, 0.3); loop(); if (!reduceMotion) setHoverId(drag.nodeId); }
    }
    if (drag.nodeId && sim) {
      const node = sim.nodes.find((row) => row.id === drag.nodeId);
      const k = cameraRef.current.k;
      if (node) { node.fx = drag.startX + dx / k; node.fy = drag.startY + dy / k; }
      if (!isActive(sim)) loop();
    } else if (!drag.nodeId) {
      touchedCameraRef.current = true;
      cameraRef.current.x = drag.startX + dx; cameraRef.current.y = drag.startY + dy;
      applyCamera();
    }
  };
  const onPointerUp = (event: PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    const viewport = viewportRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    dragRef.current = null;
    viewport?.classList.remove("is-panning", "is-dragging-node");
    if (viewport?.hasPointerCapture(event.pointerId)) viewport.releasePointerCapture(event.pointerId);
    if (!drag.moved) return;
    suppressClickRef.current = true;
    window.setTimeout(() => { suppressClickRef.current = false; }, 0);
    const sim = simRef.current;
    if (drag.nodeId && sim) {
      // Let go: the person floats back into balance with their connections.
      const node = sim.nodes.find((row) => row.id === drag.nodeId);
      if (node) { node.fx = null; node.fy = null; }
      sim.alphaTarget = 0;
      setHoverId(null);
      loop();
    }
  };

  const relayout = () => {
    const sim = simRef.current;
    if (!sim) return;
    sim.nodes.forEach((node, index) => {
      const spot = seedPosition(index);
      node.x = spot.x + (Math.random() - 0.5) * 30; node.y = spot.y + (Math.random() - 0.5) * 30; node.vx = 0; node.vy = 0;
    });
    touchedCameraRef.current = false;
    reheat(sim, 1);
    loop();
  };

  const highlighted = (id: string) => !neighbours || neighbours.has(id);
  return <div className="graph-canvas">
    <div className="people-graph-viewport graph-live" ref={viewportRef} onPointerDown={onPointerDown} onPointerMove={onPointerMove}
      onPointerUp={onPointerUp} onPointerCancel={onPointerUp}
      onClickCapture={(event) => { if (suppressClickRef.current) { event.stopPropagation(); event.preventDefault(); } }}>
      <div className="people-graph-world" ref={worldRef} role="group"
        aria-label={`Relationship graph with ${people.length} people and ${edges.length} connections`}>
        <svg className="graph-edges" aria-hidden="true">
          <defs><marker id="graph-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M0 1 L9 5 L0 9 z" fill="currentColor" /></marker></defs>
          {edges.map(({ relation, from, to }) => {
            const style = edgeStyle(relation);
            const active = focusId !== null && (from === focusId || to === focusId);
            const dim = neighbours !== null && !active && selectedEdgeId !== relation.id;
            const showLabel = Boolean(style.label) && (active || selectedEdgeId === relation.id);
            return <g key={relation.id} className={`graph-edge${active ? " is-active" : ""}${dim ? " is-dim" : ""}${selectedEdgeId === relation.id ? " is-selected" : ""}`}
              data-axis={style.axis ?? "known"}>
              <line ref={(line) => { edgeRefs.current.set(relation.id, { ...(edgeRefs.current.get(relation.id) ?? { hit: null, label: null }), line }); }}
                style={{ strokeWidth: style.width }} markerEnd="url(#graph-arrow)" />
              <line className="graph-edge-hit" onClick={() => onSelectEdge(relation.id)}
                ref={(hit) => { edgeRefs.current.set(relation.id, { ...(edgeRefs.current.get(relation.id) ?? { line: null, label: null }), hit }); }} />
              {showLabel && <text ref={(label) => { edgeRefs.current.set(relation.id, { ...(edgeRefs.current.get(relation.id) ?? { line: null, hit: null }), label }); }}>{style.label}</text>}
            </g>;
          })}
        </svg>
        {people.map((person) => {
          const r = radiusOf(person);
          const avatar = portraitUrl(person.attributes?.avatar_url);
          const crop = faceCrop(person.attributes);
          const isPlayer = person.name === protagonistName;
          return <button type="button" key={person.id} data-node-id={person.id}
            ref={(element) => { if (element) nodeRefs.current.set(person.id, element); else nodeRefs.current.delete(person.id); }}
            className={`graph-node${highlighted(person.id) ? "" : " is-dim"}${focusId === person.id ? " is-focus" : ""}`}
            data-player={isPlayer} data-importance={importanceOf(person).toLowerCase()} aria-pressed={selectedId === person.id}
            style={{ "--r": `${r}px` } as React.CSSProperties}
            onClick={() => onSelectPerson(person.id)} onPointerEnter={() => { if (!dragRef.current) setHoverId(person.id); }}
            onPointerLeave={() => { if (!dragRef.current) setHoverId(null); }} onFocus={() => setHoverId(person.id)} onBlur={() => setHoverId(null)}>
            <span className={`graph-node-dot${crop.className}`} style={crop.style}>
              {avatar ? <Image src={avatar} alt="" width={r * 2} height={r * 2} unoptimized draggable={false} /> : <span>{person.name.charAt(0).toLocaleUpperCase()}</span>}
            </span>
            <span className="graph-node-label"><strong>{person.name}</strong>
              <small>{person.role || (isPlayer ? "Player character" : "")}</small></span>
          </button>;
        })}
      </div>
    </div>
    {!edges.length && <div className="graph-empty"><Spot name="graph-empty" size={40} />
      <p>{filtered ? "No connections match these filters." : "No connections recorded yet. They appear as people trust, fear, or clash with each other."}</p></div>}
    <div className="graph-controls" aria-label="Graph view">
      <button type="button" aria-label="Zoom out" disabled={zoom <= MIN_ZOOM} onClick={() => zoomAt(cameraRef.current.k / 1.25)}><Minus size={14} /></button>
      <span aria-live="polite">{Math.round(zoom * 100)}%</span>
      <button type="button" aria-label="Zoom in" disabled={zoom >= MAX_ZOOM} onClick={() => zoomAt(cameraRef.current.k * 1.25)}><Plus size={14} /></button>
      <button type="button" aria-label="Fit everyone in view" title="Fit" onClick={() => { touchedCameraRef.current = false; fitView(); }}><Maximize2 size={14} /></button>
      <button type="button" aria-label="Shuffle layout" title="Shuffle layout" onClick={relayout}><Shuffle size={14} /></button>
    </div>
  </div>;
}
