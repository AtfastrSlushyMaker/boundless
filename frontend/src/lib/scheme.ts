"use client";

import { useEffect, useState } from "react";
import { SCHEME_KEY as KEY } from "@/lib/schemeBoot";

export type SchemePreference = "system" | "light" | "dark";


function resolve(preference: SchemePreference): "light" | "dark" {
  if (preference !== "system") return preference;
  return typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

function readPreference(): SchemePreference {
  try {
    const stored = window.localStorage.getItem(KEY);
    return stored === "light" || stored === "dark" ? stored : "system";
  } catch { return "system"; }
}

export function useScheme(): { preference: SchemePreference; scheme: "light" | "dark"; setPreference: (value: SchemePreference) => void } {
  const [preference, setPreferenceState] = useState<SchemePreference>("system");
  const [scheme, setScheme] = useState<"light" | "dark">("dark");
  useEffect(() => {
    const timer = window.setTimeout(() => {
      const stored = readPreference();
      setPreferenceState(stored);
      setScheme(resolve(stored));
    }, 0);
    return () => window.clearTimeout(timer);
  }, []);
  useEffect(() => {
    const apply = () => {
      const next = resolve(preference);
      document.documentElement.dataset.scheme = next;
      setScheme(next);
    };
    apply();
    if (preference !== "system") return;
    const media = window.matchMedia("(prefers-color-scheme: light)");
    media.addEventListener("change", apply);
    return () => media.removeEventListener("change", apply);
  }, [preference]);
  const setPreference = (value: SchemePreference) => {
    setPreferenceState(value);
    try { window.localStorage.setItem(KEY, value); } catch { /* per-device convenience */ }
  };
  return { preference, scheme, setPreference };
}
