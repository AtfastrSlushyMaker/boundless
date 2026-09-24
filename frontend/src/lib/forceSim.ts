/**
 * A small force-directed layout (repulsion, springs, gravity, collision) in the spirit of
 * d3-force. Nodes settle as alpha cools; dragging reheats it so neighbours follow.
 */

export type SimNode = { id: string; x: number; y: number; vx: number; vy: number; r: number; fx?: number | null; fy?: number | null };
export type SimLink = { source: number; target: number };

export type Sim = {
  nodes: SimNode[];
  links: SimLink[];
  alpha: number;
  alphaTarget: number;
};

const ALPHA_MIN = 0.002;
const ALPHA_DECAY = 1 - Math.pow(ALPHA_MIN, 1 / 260);
const VELOCITY_KEEP = 0.58;
const CHARGE = -1500;
const CHARGE_RANGE2 = 380 * 380;
const LINK_STRENGTH = 0.08;
const GRAVITY = 0.04;
// People with no known connections drift to a loose ring nearby instead of the far edges.
const ORPHAN_GRAVITY = 0.09;

export function createSim(nodes: SimNode[], links: SimLink[], alpha = 1): Sim {
  return { nodes, links, alpha, alphaTarget: 0 };
}

export function isActive(sim: Sim): boolean {
  return sim.alpha > ALPHA_MIN || sim.alphaTarget > 0;
}

export function reheat(sim: Sim, alpha = 0.6) {
  sim.alpha = Math.max(sim.alpha, alpha);
}

/** Place a node that has no remembered position: beside a neighbour if it has one, else on a spiral. */
export function seedPosition(index: number, neighbour?: { x: number; y: number }): { x: number; y: number } {
  if (neighbour) {
    const angle = Math.random() * Math.PI * 2;
    return { x: neighbour.x + Math.cos(angle) * 60, y: neighbour.y + Math.sin(angle) * 60 };
  }
  const radius = 42 * Math.sqrt(index + 0.5);
  const angle = index * Math.PI * (3 - Math.sqrt(5));
  return { x: Math.cos(angle) * radius, y: Math.sin(angle) * radius };
}

export function tick(sim: Sim) {
  const { nodes, links } = sim;
  sim.alpha += (sim.alphaTarget - sim.alpha) * ALPHA_DECAY;
  const alpha = sim.alpha;
  const count = nodes.length;

  // Every pair repels, softened at short range so overlapping nodes do not explode apart.
  for (let i = 0; i < count; i += 1) {
    const a = nodes[i];
    for (let j = i + 1; j < count; j += 1) {
      const b = nodes[j];
      let dx = b.x - a.x, dy = b.y - a.y;
      if (dx === 0 && dy === 0) { dx = (Math.random() - 0.5) * 1e-3; dy = (Math.random() - 0.5) * 1e-3; }
      const d2 = Math.max(dx * dx + dy * dy, 900);
      if (d2 > CHARGE_RANGE2) continue;
      const force = (CHARGE * alpha) / d2;
      a.vx += dx * force * 0.5; a.vy += dy * force * 0.5;
      b.vx -= dx * force * 0.5; b.vy -= dy * force * 0.5;
    }
  }

  // Connections pull toward a comfortable length, weaker for well-connected nodes.
  const degree = new Array(count).fill(0);
  for (const link of links) { degree[link.source] += 1; degree[link.target] += 1; }
  for (const link of links) {
    const a = nodes[link.source], b = nodes[link.target];
    const dx = b.x + b.vx - a.x - a.vx, dy = b.y + b.vy - a.y - a.vy;
    const distance = Math.hypot(dx, dy) || 1;
    const rest = 120 + a.r + b.r;
    const pull = ((distance - rest) / distance) * alpha * LINK_STRENGTH * 4 / Math.min(degree[link.source], degree[link.target]);
    const bias = degree[link.source] / (degree[link.source] + degree[link.target]);
    b.vx -= dx * pull * bias; b.vy -= dy * pull * bias;
    a.vx += dx * pull * (1 - bias); a.vy += dy * pull * (1 - bias);
  }

  nodes.forEach((node, index) => {
    const gravity = degree[index] ? GRAVITY : ORPHAN_GRAVITY;
    node.vx -= node.x * gravity * alpha;
    node.vy -= node.y * gravity * alpha;
  });

  for (const node of nodes) {
    if (node.fx != null && node.fy != null) {
      node.x = node.fx; node.y = node.fy; node.vx = 0; node.vy = 0;
      continue;
    }
    node.vx *= VELOCITY_KEEP; node.vy *= VELOCITY_KEEP;
    node.x += node.vx; node.y += node.vy;
  }

  // Keep circles and their name labels from overlapping.
  for (let i = 0; i < count; i += 1) {
    const a = nodes[i];
    for (let j = i + 1; j < count; j += 1) {
      const b = nodes[j];
      const dx = b.x - a.x, dy = b.y - a.y;
      const minimum = a.r + b.r + 26;
      const d2 = dx * dx + dy * dy;
      if (d2 >= minimum * minimum) continue;
      const distance = Math.sqrt(d2) || 0.01;
      const push = ((minimum - distance) / distance) * 0.5;
      const aFixed = a.fx != null, bFixed = b.fx != null;
      if (!aFixed) { a.x -= dx * push * (bFixed ? 2 : 1); a.y -= dy * push * (bFixed ? 2 : 1); }
      if (!bFixed) { b.x += dx * push * (aFixed ? 2 : 1); b.y += dy * push * (aFixed ? 2 : 1); }
    }
  }
}
