"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { ChevronDown, CloudAlert, LoaderCircle, RefreshCw, Save } from "lucide-react";
import { useEffect, useState } from "react";
import { api, ModelRole, ModelRoleSetting, ModelSettings } from "@/lib/api";

const ROLE_COPY: Record<Exclude<ModelRole, "state_fallback">, { label: string; inherit: string; help: string }> = {
  state: { label: "State tracking", inherit: "Same as narrator", help: "Reads each turn and updates people, items, places, and objectives." },
  summary: { label: "Summary and memory", inherit: "Same as state tracking", help: "Keeps the rolling campaign summary current." },
  canon_repair: { label: "Canon repair", inherit: "Same as narrator", help: "Rewrites a passage that breaks a hard rule." },
  mature: { label: "Mature scenes", inherit: "Off · use narrator",
    help: "Writes explicit scenes between adults and describes how characters look. The narrator still decides what happens; this model writes it without toning it down. Pick an uncensored local model." },
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
const CUSTOM = "__custom__";

/** Model picker filled from what the chosen runtime reports as installed; free text stays available. */
function RoleModelField({ provider, baseUrl, value, onChange }: {
  provider: ModelSettings["provider"]; baseUrl: string; value: string; onChange: (model: string) => void;
}) {
  const [endpoint, setEndpoint] = useState(baseUrl);
  const [typing, setTyping] = useState(false);
  useEffect(() => {
    const timer = window.setTimeout(() => setEndpoint(baseUrl), 450);
    return () => window.clearTimeout(timer);
  }, [baseUrl]);
  const validEndpoint = /^https?:\/\/\S+/i.test(endpoint);
  const models = useQuery({
    queryKey: ["role-models", provider, provider === "deepseek" ? "" : endpoint],
    queryFn: async () => {
      if (provider === "deepseek") return (await api.deepseekModels()).models;
      if (provider === "ollama") return (await api.ollamaModels(endpoint)).models;
      if (provider === "mlx") {
        const [runtime, served] = await Promise.allSettled([api.mlxRuntime(), api.compatibleModels(endpoint)]);
        const names = [...(runtime.status === "fulfilled" ? runtime.value.models.map((entry) => entry.id) : []),
          ...(served.status === "fulfilled" ? served.value.models : [])];
        if (!names.length && served.status === "rejected") throw served.reason;
        return Array.from(new Set(names));
      }
      return (await api.compatibleModels(endpoint)).models;
    },
    enabled: provider === "deepseek" || validEndpoint,
    retry: false,
    staleTime: 30_000,
  });
  const options = models.data ?? [];
  const listed = options.includes(value);
  const showSelect = options.length > 0 && !typing && (listed || !value);
  return <label className="role-model-field"><span>Model</span>
    <span className="role-model-control">
      {showSelect
        ? <select value={listed ? value : ""} onChange={(event) => {
          if (event.target.value === CUSTOM) { setTyping(true); return; }
          onChange(event.target.value);
        }}>
          {!listed && <option value="" disabled>Choose an installed model…</option>}
          {options.map((name) => <option key={name} value={name} title={name}>{name.split("/").filter(Boolean).pop() ?? name}</option>)}
          <option value={CUSTOM}>Other (type a name)…</option>
        </select>
        : <input value={value} placeholder="Model identifier" onChange={(event) => onChange(event.target.value)} spellCheck={false}
          list={options.length ? `role-models-${provider}` : undefined} />}
      <button type="button" className="role-model-refresh" aria-label="Reload installed models" title="Reload installed models"
        disabled={models.isFetching} onClick={() => { setTyping(false); void models.refetch(); }}>
        {models.isFetching ? <LoaderCircle size={14} className="spin" /> : <RefreshCw size={14} />}</button>
    </span>
    {options.length > 0 && <datalist id={`role-models-${provider}`}>{options.map((name) => <option key={name} value={name} />)}</datalist>}
    <small className="role-model-status">{models.isFetching ? "Looking for installed models…"
      : models.isError ? `Could not list models${provider === "deepseek" ? " (save a DeepSeek key first)" : " from this endpoint"}. Type the name instead.`
      : models.isSuccess && !options.length ? "No installed models reported. Type the name instead."
      : options.length ? `${options.length} model${options.length === 1 ? "" : "s"} available` : ""}</small>
  </label>;
}

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
        roles: (["state", "summary", "canon_repair", "mature", "state_fallback"] as ModelRole[]).map((role) => ({
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
  const hostedRoles = form ? (["state", "summary", "canon_repair", "mature"] as const).filter((role) => !form[role].inherit && hostedProvider(form[role])) : [];
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
    <RoleModelField provider={form[role].provider} baseUrl={form[role].base_url || DEFAULT_ENDPOINTS[form[role].provider]}
      value={form[role].model} onChange={(model) => update(role, { model })} />
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
          {(["state", "summary", "canon_repair", "mature"] as const).map((role) => <div className="role-row" key={role}>
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
