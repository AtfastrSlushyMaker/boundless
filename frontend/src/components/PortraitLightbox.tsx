"use client";

import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { ExternalLink, X } from "lucide-react";
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

type Portrait = { src: string; name: string; caption?: string };
type Focus = { x?: number; y?: number; size?: number; aspect?: number };

/** Thumbnail crop that zooms a full-length portrait onto the face; the lightbox still shows the whole figure. */
export function faceCrop(attributes?: Record<string, unknown> | null): { className: string; style?: React.CSSProperties } {
  const focus = (attributes?.portrait_focus ?? null) as Focus | null;
  if (!focus && attributes?.portrait_framing !== "full_body") return { className: "" };
  const x = focus?.x ?? 0.5, y = focus?.y ?? 0.125, size = Math.max(focus?.size ?? 0.085, 0.03), aspect = focus?.aspect ?? 832 / 1216;
  const scale = Math.max(0.46 / size, 1 / aspect, 1);
  const clamp = (value: number, min: number) => Math.min(0, Math.max(min, value));
  return { className: " has-face-crop", style: {
    "--face-h": `${(scale * 100).toFixed(2)}%`,
    "--face-l": `${(clamp(0.5 - x * scale * aspect, 1 - scale * aspect) * 100).toFixed(2)}%`,
    "--face-t": `${(clamp(0.5 - y * scale, 1 - scale) * 100).toFixed(2)}%`,
  } as React.CSSProperties };
}

/** A portrait thumbnail that opens full size, uncropped, over everything else. */
export function PortraitButton({ src, name, caption, className, style, children }: Portrait & { className: string; style?: React.CSSProperties; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  return <>
    <button type="button" className={`${className} portrait-zoomable`} style={style} onClick={() => setOpen(true)}
      aria-label={`View ${name}'s portrait full size`} title="View full size">{children}</button>
    <PortraitLightbox portrait={open ? { src, name, caption } : null} onClose={() => setOpen(false)} />
  </>;
}

export function PortraitLightbox({ portrait, onClose }: { portrait: Portrait | null; onClose: () => void }) {
  const reduceMotion = useReducedMotion();
  useEffect(() => {
    if (!portrait) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") { event.stopPropagation(); onClose(); } };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [portrait, onClose]);
  if (typeof document === "undefined") return null;
  return createPortal(<AnimatePresence>
    {portrait && <motion.div key="lightbox" className="portrait-lightbox" role="dialog" aria-modal="true" aria-label={`${portrait.name} portrait`}
      onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}
      initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: reduceMotion ? 0 : 0.2 }}>
      <motion.figure initial={{ opacity: 0, scale: reduceMotion ? 1 : 0.94, y: reduceMotion ? 0 : 10 }} animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: reduceMotion ? 1 : 0.97 }} transition={{ type: "spring", stiffness: 320, damping: 30 }}>
        {/* eslint-disable-next-line @next/next/no-img-element -- local portrait served by the API at native size */}
        <img src={portrait.src} alt={`Portrait of ${portrait.name}`} />
        <figcaption><div><strong>{portrait.name}</strong>{portrait.caption && <span>{portrait.caption}</span>}</div>
          <a href={portrait.src} target="_blank" rel="noopener noreferrer"><ExternalLink size={14} />Open original</a></figcaption>
      </motion.figure>
      <button type="button" className="portrait-lightbox-close" aria-label="Close portrait" onClick={onClose} autoFocus><X size={20} /></button>
    </motion.div>}
  </AnimatePresence>, document.body);
}
