"use client";

import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { ExternalLink, X } from "lucide-react";
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

type Portrait = { src: string; name: string; caption?: string };

/** A portrait thumbnail that opens full size, uncropped, over everything else. */
export function PortraitButton({ src, name, caption, className, children }: Portrait & { className: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  return <>
    <button type="button" className={`${className} portrait-zoomable`} onClick={() => setOpen(true)}
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
