"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { ChevronDown, CloudAlert, LoaderCircle, Save } from "lucide-react";
import { useState } from "react";
import { api, ModelRole, ModelRoleSetting, ModelSettings } from "@/lib/api";

const ROLE_COPY: Record<Exclude<ModelRole, "state_fallback">, { label: string; inherit: string; help: string }> = {
  state: { label: "State tracking", inherit: "Same as narrator", help: "Reads each turn and updates people, items, places, and objectives." },
  summary: { label: "Summary and memory", inherit: "Same as state tracking", help: "Keeps the rolling campaign summary current." },
  canon_repair: { label: "Canon repair", inherit: "Same as narrator", help: "Rewrites a passage that breaks a hard rule." },
};
const DEFAULT_ENDPOINTS: Record<ModelSettings["provider"], string> = {
  mlx: "http://127.0.0.1:8088/v1", ollama: "http://127.0.0.1:11434", "openai-compatible": "http://127.0.0.1:1234/v1",
  deepseek: "https://api.deepseek.com",
};

function hostedProvider(role: Pick<ModelRoleSetting, "provider" | "base_url">): boolean {
  if (role.provider === "deepseek") return true;
  if (role.provider !== "openai-compatible") return false;
  try {
    const host = new URL(role.base_url).hostname;
    return !(host === "localhost" || host.startsWith("127.") || host.startsWith("192.168.") || host.startsWith("10.") || host === "host.docker.internal");
  } catch { return false; }
}

type Draft = Record<ModelRole, ModelRoleSetting>;

export function ModelRolesPanel({ narratorLabel }: { narratorLabel: string }) {
  const client = useQueryClient();
  const reduceMotion = useReducedMotion();
  const [open, setOpen] = useState(false);
  const roles = useQuery({ queryKey: ["model-roles"], queryFn: api.modelRoles, enabled: open });
  const [draft, setDraft] = useState<Draft | null>(null);
  const [fallback, setFallback] = useState<boolean | null>(null);
  const [message, setMessage] = useState("");
  const base = roles.data ? Object.fromEntries(roles.data.roles.map((row) => [row.role, row])) as Draft : null;
  const form = draft ?? base;
  const fallbackOn = fallback ?? roles.data?.hosted_fallback_enabled ?? false;
  const update = (role: ModelRole, patch: Partial<ModelRoleSetting>) => {
    if (!form) return;
    setDraft({ ...form, [role]: { ...form[role], ...patch } });
  };
  const save = useMutation({
    mutationFn: () => {
      if (!form) throw new Error("Roles are still loading.");
      return api.saveModelRoles({
        roles: (["state", "summary", "canon_repair", "state_fallback"] as ModelRole[]).map((role) => ({
          role, inherit: role === "state_fallback" ? !fallbackOn : form[role].inherit, provider: form[role].provider,
          base_url: form[role].base_url || DEFAULT_ENDPOINTS[form[role].provider], model: form[role].model,
          temperature: form[role].temperature, context_window: form[role].context_window,
        })),
        hosted_fallback_enabled: fallbackOn,
      });
    },
    onSuccess: async () => {
      setDraft(null); setFallback(null); setMessage("AI roles saved.");
      await client.invalidateQueries({ queryKey: ["model-roles"] });
    },
    onError: (error) => setMessage(error.message),
  });
  const hostedRoles = form ? (["state", "summary", "canon_repair"] as const).filter((role) => !form[role].inherit && hostedProvider(form[role])) : [];
  const narratorLocal = roles.data ? !roles.data.narrator_hosted : true;

  const roleFields = (role: ModelRole) => form && <div className="role-fields">
    <label><span>Runtime</span>
      <select value={form[role].provider} onChange={(event) => {
        const provider = event.target.value as ModelSettings["provider"];
        update(role, { provider, base_url: DEFAULT_ENDPOINTS[provider], model: provider === "deepseek" ? "deepseek-flash" : form[role].model });
      }}>
        <option value="mlx">Apple Silicon · MLX</option><option value="ollama">Ollama</option>
        <option value="openai-compatible">OpenAI-compatible API</option><option value="deepseek">DeepSeek (hosted)</option>
      </select></label>
    {form[role].provider !== "deepseek" && <label><span>Endpoint</span>
      <input value={form[role].base_url || DEFAULT_ENDPOINTS[form[role].provider]} onChange={(event) => update(role, { base_url: event.target.value })} spellCheck={false} /></label>}
    <label><span>Model</span><input value={form[role].model} placeholder="Model identifier" onChange={(event) => update(role, { model: event.target.value })} spellCheck={false} /></label>
  </div>;

  return <section className={`model-roles${open ? " is-open" : ""}`}>
    <button type="button" className="model-roles-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
      <span><strong>Advanced · AI roles</strong><small>Use different models for narration and bookkeeping.</small></span>
      <motion.span animate={{ rotate: open ? 180 : 0 }} transition={{ duration: reduceMotion ? 0 : 0.2 }}><ChevronDown size={16} /></motion.span>
    </button>
    <AnimatePresence initial={false}>
      {open && <motion.div key="roles" className="model-roles-body" initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }}
        exit={{ height: 0, opacity: 0 }} transition={{ duration: reduceMotion ? 0 : 0.24, ease: [0.22, 1, 0.36, 1] }}>
        {roles.isLoading && <p className="field-help"><LoaderCircle size={13} className="spin" /> Loading roles…</p>}
        {form && <div className="model-roles-grid">
          <div className="role-row role-row--fixed"><div><strong>Narrator</strong><small>Tells the story.</small></div><span className="role-chip">{narratorLabel}</span></div>
          {(["state", "summary", "canon_repair"] as const).map((role) => <div className="role-row" key={role}>
            <div><strong>{ROLE_COPY[role].label}</strong><small>{ROLE_COPY[role].help}</small></div>
            <select aria-label={`${ROLE_COPY[role].label} model`} value={form[role].inherit ? "inherit" : "custom"}
              onChange={(event) => update(role, { inherit: event.target.value === "inherit" })}>
              <option value="inherit">{ROLE_COPY[role].inherit}</option><option value="custom">Choose a model…</option>
            </select>
            <AnimatePresence initial={false}>{!form[role].inherit && <motion.div key="fields" initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }}>{roleFields(role)}</motion.div>}</AnimatePresence>
          </div>)}
          <div className="role-row">
            <div><strong>Hosted fallback for state tracking</strong><small>Only when the state model cannot produce usable output after a repair attempt.</small></div>
            <label className="role-switch"><input type="checkbox" checked={fallbackOn} onChange={(event) => setFallback(event.target.checked)} /><span>{fallbackOn ? "Enabled" : "Disabled"}</span></label>
            <AnimatePresence initial={false}>{fallbackOn && <motion.div key="fallback" initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }}>{roleFields("state_fallback")}</motion.div>}</AnimatePresence>
          </div>
        </div>}
        {(hostedRoles.length > 0 || fallbackOn) && <div className="privacy-callout" role="note">
          <CloudAlert size={16} />
          <p>{narratorLocal ? "Your narrator runs locally, but " : ""}{[...hostedRoles.map((role) => ROLE_COPY[role].label.toLowerCase()), ...(fallbackOn ? ["the fallback"] : [])].join(" and ")} {hostedRoles.length + (fallbackOn ? 1 : 0) > 1 ? "use" : "uses"} a hosted provider.
            It will receive the player&apos;s action, the GM narration, and the relevant campaign state for each turn it handles.</p>
        </div>}
        {message && <p className="form-message" role="status">{message}</p>}
        <div className="model-roles-actions"><button type="button" className="quiet-button" disabled={save.isPending || !form} onClick={() => save.mutate()}>
          {save.isPending ? <LoaderCircle size={15} className="spin" /> : <Save size={15} />}<span>Save AI roles</span></button></div>
      </motion.div>}
    </AnimatePresence>
  </section>;
}
