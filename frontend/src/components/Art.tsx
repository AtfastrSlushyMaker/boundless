/* eslint-disable @next/next/no-img-element -- static illustrations served from /public at their own size */

export type SpotName = "first-scene" | "generating" | "graph-empty" | "no-groups" | "no-people" | "no-worlds" | "notebook-empty" | "portrait-missing";

/** An ink vignette for empty and waiting moments; adapts to light and dark. */
export function Spot({ name, size = 140, className = "" }: { name: SpotName; size?: number; className?: string }) {
  return <img src={`/art/spots/${name}.webp`} alt="" aria-hidden="true" className={`spot ${className}`} width={size} height={size}
    style={{ ["--spot-size" as string]: `${size}px` }} loading="lazy" decoding="async" />;
}

const EMBLEMS = new Set(["faction", "nation", "city", "guild", "religion", "house", "military", "government", "crew"]);

/** The engraved seal for a kind of group (faction, nation, faith…). */
export function Emblem({ kind, className = "" }: { kind: string; className?: string }) {
  return <img src={`/art/emblems/${EMBLEMS.has(kind) ? kind : "faction"}.webp`} alt="" aria-hidden="true" className={`emblem ${className}`}
    loading="lazy" decoding="async" />;
}

/** The Boundless mark (engraved B). */
export function Mark({ size = 28, className = "" }: { size?: number; className?: string }) {
  return <img src="/art/mark.webp" alt="" aria-hidden="true" className={`brand-mark ${className}`} width={size} height={size} />;
}

export const plateUrl = (family?: string | null) => `/art/plates/${family || "neutral"}.webp`;
