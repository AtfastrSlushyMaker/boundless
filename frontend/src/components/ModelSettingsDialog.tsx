"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, CircleX, LoaderCircle, Save, X } from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useCallback, useEffect, useState } from "react";
import { api, ModelSettings } from "@/lib/api";
import { ImageSettingsPanel } from "@/components/ImageSettingsPanel";
import { ModelRolesPanel } from "@/components/ModelRolesPanel";

type Props = { open: boolean; onClose: () => void };

const initial: ModelSettings = {
  provider: "mlx", model: "lukey03/Qwen3.5-9B-abliterated-MLX-4bit",
  base_url: "http://127.0.0.1:8088/v1", context_window: 131072,
  response_length: "standard", temperature: 0.82,
};

function deepseekModelLabel(model: string): string {
  if (model === "deepseek-flash") return "DeepSeek V4.1 Flash";
  if (model === "deepseek-v4-pro") return "DeepSeek V4 Pro";
  return model;
}

function modelDisplayLabel(provider: ModelSettings["provider"], model: string): string {
  if (provider === "deepseek") return deepseekModelLabel(model);
  if (provider === "mlx") return model.split("/").filter(Boolean).at(-1) ?? model;
  return model;
}

function isLoopbackEndpoint(endpoint: string): boolean {
  try {
    const hostname = new URL(endpoint).hostname.replace(/^\[|\]$/g, "").toLowerCase();
    return hostname === "localhost" || hostname === "::1" || hostname.startsWith("127.");
  } catch { return false; }
}

function supportsCreativity(provider: ModelSettings["provider"], model: string): boolean {
  return Boolean(model.trim()) && ["mlx", "ollama", "openai-compatible", "deepseek"].includes(provider);
}

const contextChoices = [8_192, 16_384, 32_768, 65_536, 131_072, 262_144, 524_288, 1_000_000];
function contextLabel(value: number): string {
  return value === 1_000_000 ? "1M tokens" : `${Math.round(value / 1024)}K tokens`;
}

export function ModelSettingsDialog({ open, onClose }: Props) {
  const client = useQueryClient();
  const reduceMotion = useReducedMotion();
  const query = useQuery({ queryKey: ["model-settings"], queryFn: api.settings, enabled: open });
  const capabilities = useQuery({ queryKey: ["system-capabilities"], queryFn: api.capabilities, enabled: open, staleTime: 300_000 });
  const [draft, setDraft] = useState<ModelSettings | null>(null);
  const [section, setSection] = useState<"model" | "portraits">("model");
  const [editModelId, setEditModelId] = useState(false);
  const [compatibleEndpoint, setCompatibleEndpoint] = useState("");
  const [deepseekApiKey, setDeepseekApiKey] = useState("");
  const [message, setMessage] = useState("");
  const closeDialog = useCallback(() => {
    setDeepseekApiKey("");
    setDraft(null);
    setMessage("");
    onClose();
  }, [onClose]);
  const form: ModelSettings = { ...initial, ...(draft ?? query.data ?? {}) };
  const setForm = (update: (value: ModelSettings) => ModelSettings) => setDraft(update(form));
  const mlxSupported = capabilities.data?.mlx_supported ?? false;
  const mlxDefaultEndpoint = capabilities.data?.mlx_default_base_url ?? initial.base_url;
  const isLocalModel = form.provider === "mlx" || form.provider === "ollama" || (
    form.provider === "openai-compatible" && isLoopbackEndpoint(form.base_url)
  );
  useEffect(() => {
    if (!open || form.provider !== "openai-compatible") return;
    const timer = window.setTimeout(() => setCompatibleEndpoint(form.base_url), 450);
    return () => window.clearTimeout(timer);
  }, [open, form.base_url, form.provider]);
  const compatibleModels = useQuery({
    queryKey: ["compatible-models", compatibleEndpoint],
    queryFn: () => api.compatibleModels(compatibleEndpoint),
    enabled: open && form.provider === "openai-compatible" && /^https?:\/\//i.test(compatibleEndpoint),
    retry: false,
    staleTime: 30_000,
  });
  const availableCompatibleModels = compatibleModels.data?.models ?? [];
  const ollamaModels = useQuery({
    queryKey: ["ollama-models", form.base_url],
    queryFn: () => api.ollamaModels(form.base_url),
    enabled: open && form.provider === "ollama",
    retry: false,
    staleTime: 30_000,
  });
  const installedModels = ollamaModels.data?.models ?? [];
  const mlxRuntime = useQuery({
    queryKey: ["mlx-runtime"],
    queryFn: api.mlxRuntime,
    enabled: open && form.provider === "mlx" && mlxSupported,
    refetchInterval: open && form.provider === "mlx" ? 3_000 : false,
    retry: false,
  });
  const mlxModels = useQuery({
    queryKey: ["mlx-models", form.base_url],
    queryFn: () => api.compatibleModels(form.base_url),
    enabled: open && form.provider === "mlx" && mlxSupported,
    retry: false,
    refetchInterval: (query) => open && form.provider === "mlx" && query.state.status !== "success" ? 3_000 : false,
    staleTime: 30_000,
  });
  const installedMlxModels = mlxRuntime.data?.models ?? [];
  const availableMlxModels = Array.from(new Set([
    ...installedMlxModels.map((entry) => entry.id), ...(mlxModels.data?.models ?? []),
  ]));
  const mlxUsesDefaultEndpoint = form.base_url.replace(/\/$/, "") === mlxDefaultEndpoint.replace(/\/$/, "");
  const mlxServerRunning = mlxUsesDefaultEndpoint && mlxRuntime.data?.launcher_available
    ? mlxRuntime.data.server_status === "running" : mlxModels.isSuccess;
  const selectedMlxModel = installedMlxModels.length === 1 && (form.model === initial.model || !form.model.trim())
    ? installedMlxModels[0].id : form.model;
  const selectedMlxInstalled = installedMlxModels.some((entry) => entry.id === selectedMlxModel);
  useEffect(() => {
    if (mlxRuntime.data?.server_status === "running") {
      void client.invalidateQueries({ queryKey: ["mlx-models"] });
      void client.invalidateQueries({ queryKey: ["model-settings"] });
    }
  }, [client, mlxRuntime.data?.server_status]);
  const selectedOllamaModel = installedModels.find((name) => name.toLowerCase() === form.model.toLowerCase()) ?? form.model;
  const deepseekModels = useQuery({
    queryKey: ["deepseek-models", query.data?.api_key_configured],
    queryFn: () => api.deepseekModels(deepseekApiKey.trim() || undefined),
    enabled: open && form.provider === "deepseek" && Boolean(query.data?.api_key_configured),
    retry: false,
    staleTime: 30_000,
  });
  const servedDeepseekModels = deepseekModels.data?.models ?? [];
  const connectionChanged = Boolean(query.data && (
    form.provider !== query.data.provider || form.base_url !== query.data.base_url || (form.provider === "mlx" ? selectedMlxModel : form.model) !== query.data.model || Boolean(deepseekApiKey.trim())
  ));
  const save = useMutation({
    mutationFn: () => api.saveSettings({ ...form, model: form.provider === "mlx" ? selectedMlxModel : form.model,
      api_key: form.provider === "deepseek" ? deepseekApiKey.trim() || undefined : undefined }),
    onSuccess: async () => {
      setDeepseekApiKey("");
      setMessage("Model profile saved.");
      await client.invalidateQueries({ queryKey: ["model-settings"] });
      await client.invalidateQueries({ queryKey: ["health"] });
    },
    onError: (error) => setMessage(error.message),
  });
  const startMlx = useMutation({
    mutationFn: async () => {
      const result = await api.startMlx(selectedMlxModel);
      await api.saveSettings({ ...form, provider: "mlx", model: selectedMlxModel });
      return result;
    },
    onSuccess: async () => {
      setMessage("MLX is starting. The first load may take a moment.");
      await client.invalidateQueries({ queryKey: ["mlx-runtime"] });
      await client.invalidateQueries({ queryKey: ["mlx-models"] });
      await client.invalidateQueries({ queryKey: ["model-settings"] });
      await client.invalidateQueries({ queryKey: ["health"] });
    },
    onError: (error) => setMessage(error.message),
  });
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") closeDialog(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, closeDialog]);
  return (
    <AnimatePresence initial={false}>
    {open && <motion.div key="settings-dialog" className="dialog-scrim" onMouseDown={(event) => { if (event.target === event.currentTarget) closeDialog(); }}
      initial={false} exit={{ y: reduceMotion ? 0 : 6 }} transition={{ duration: reduceMotion ? 0 : 0.16, ease: "easeOut" }}>
      <motion.section className="settings-dialog" role="dialog" aria-modal="true" aria-labelledby="settings-title"
        initial={{ y: reduceMotion ? 0 : 12 }} animate={{ y: 0 }} exit={{ y: reduceMotion ? 0 : 8 }}
        transition={{ duration: reduceMotion ? 0 : 0.2, ease: "easeOut" }}>
        <header className="dialog-head">
          <div>
            <h2 id="settings-title">Settings</h2>
          </div>
          <button className="icon-button" aria-label="Close model settings" onClick={closeDialog}><X size={18} /></button>
        </header>
        <nav className="settings-section-nav" aria-label="Settings section"><button type="button" aria-current={section === "model" ? "page" : undefined} onClick={() => setSection("model")}>Models</button><button type="button" aria-current={section === "portraits" ? "page" : undefined} onClick={() => setSection("portraits")}>Portrait generation</button></nav>
        {section === "portraits" ? <ImageSettingsPanel /> : <>
        <div className="model-health-line">
          {query.isLoading ? <LoaderCircle className="spin" size={16} /> : connectionChanged ? <CircleX size={16} /> : query.data?.health?.status === "connected" ? <Activity size={16} /> : <CircleX size={16} />}
          <span>{connectionChanged ? "Unsaved model selection" : query.data?.health?.status === "connected" ? `Connected · ${modelDisplayLabel(query.data.provider, query.data.model)}` : query.data?.health?.status === "offline" ? "Model server is offline" : query.data?.health?.status === "loading" ? "Model is loading" : query.isLoading ? "Checking saved endpoint" : "No model profile yet"}</span>
          {!connectionChanged && <button className="text-button" onClick={() => query.refetch()}>Check again</button>}
        </div>
        {!(query.isLoading && !query.data) && <form className="settings-form" onSubmit={(event) => { event.preventDefault(); save.mutate(); }}>
          <div className="settings-fields">
          <label>
            <span>Runtime</span>
            <select value={form.provider} onChange={(event) => {
              const provider = event.target.value as ModelSettings["provider"];
              setEditModelId(false);
              const saved = query.data?.provider === provider ? query.data : null;
              const endpoint = saved?.base_url ?? (provider === "mlx" ? mlxDefaultEndpoint : provider === "ollama" ? "http://127.0.0.1:11434" : provider === "deepseek" ? "https://api.deepseek.com" : "http://127.0.0.1:1234/v1");
              const model = saved?.model ?? (provider === "mlx" ? "lukey03/Qwen3.5-9B-abliterated-MLX-4bit" : provider === "ollama" ? "" : provider === "deepseek" ? "deepseek-flash" : "your-model-id");
              setForm((value) => ({ ...value, provider, base_url: endpoint, model }));
            }}>
              <optgroup label="Local runtimes">
                <option value="mlx" disabled={!mlxSupported}>Apple Silicon · MLX{mlxSupported ? "" : " · unavailable on this device"}</option>
                <option value="ollama">Ollama</option>
              </optgroup>
              <optgroup label="API endpoint"><option value="openai-compatible">OpenAI-compatible API</option></optgroup>
              <optgroup label="Cloud provider"><option value="deepseek">DeepSeek</option></optgroup>
            </select>
          </label>
          <label>
            <span>Endpoint</span>
            <input value={form.base_url} onChange={(event) => setForm((value) => ({ ...value, base_url: event.target.value }))} spellCheck={false} readOnly={form.provider === "deepseek"} required />
          </label>
          {form.provider === "mlx" && availableMlxModels.length > 0 ? <div className="model-field">
            <label>
              <span>Available MLX model</span>
              <select value={selectedMlxModel} onChange={(event) => setForm((value) => ({ ...value, model: event.target.value }))} required>
                {form.model && !availableMlxModels.includes(form.model) && <option value={form.model}>{form.model} · saved selection</option>}
                {installedMlxModels.length > 0 && <optgroup label="Installed on this Mac">
                  {installedMlxModels.map((entry) => <option value={entry.id} key={entry.id}>{entry.name}</option>)}
                </optgroup>}
                {(mlxModels.data?.models ?? []).some((name) => !installedMlxModels.some((entry) => entry.id === name)) && <optgroup label="From MLX endpoint">
                  {(mlxModels.data?.models ?? []).filter((name) => !installedMlxModels.some((entry) => entry.id === name)).map((name) => <option value={name} key={name}>{name}</option>)}
                </optgroup>}
              </select>
            </label>
          </div> : form.provider === "ollama" ? <div className="model-field">
            <label>
              <span>Installed Ollama model</span>
              <select value={selectedOllamaModel} onChange={(event) => setForm((value) => ({ ...value, model: event.target.value }))} required>
                <option value="">{ollamaModels.isLoading ? "Loading installed models…" : "Choose a model"}</option>
                {form.model && !installedModels.some((name) => name.toLowerCase() === form.model.toLowerCase()) && <option value={form.model}>{form.model} · saved selection</option>}
                {installedModels.map((name) => <option value={name} key={name}>{name}</option>)}
              </select>
            </label>
            {ollamaModels.isError && <span className="field-help field-help--error">{ollamaModels.error.message}</span>}
            {ollamaModels.isSuccess && installedModels.length === 0 && <span className="field-help">No installed models found at this endpoint.</span>}
            <button type="button" className="text-button model-refresh" onClick={() => void ollamaModels.refetch()}>Refresh installed models</button>
          </div> : form.provider === "deepseek" ? <div className="model-field">
            <label>
              <span>DeepSeek model</span>
              <select value={form.model} onChange={(event) => setForm((value) => ({ ...value, model: event.target.value }))} required>
                {form.model && !servedDeepseekModels.includes(form.model) && <option value={form.model}>{deepseekModelLabel(form.model)} · {form.model}</option>}
                {servedDeepseekModels.map((name) => <option value={name} key={name}>{deepseekModelLabel(name)} · {name}</option>)}
              </select>
            </label>
            <label>
              <span>DeepSeek API key</span>
              <input type="password" value={deepseekApiKey} onChange={(event) => setDeepseekApiKey(event.target.value)} placeholder={query.data?.api_key_configured ? "Saved key on this device" : "Enter your API key"} autoComplete="off" spellCheck={false} />
            </label>
            <span className="field-help">{query.data?.api_key_configured ? "A key is saved locally. Leave this blank to keep it, or enter a replacement." : "Enter a key to load the available models and connect."}</span>
            {deepseekModels.isError && <span className="field-help field-help--error">{deepseekModels.error.message}</span>}
            <button type="button" className="text-button model-refresh" disabled={deepseekModels.isFetching || (!deepseekApiKey.trim() && !query.data?.api_key_configured)} onClick={() => void deepseekModels.refetch()}>{deepseekModels.isFetching ? "Loading models…" : "Load available models"}</button>
          </div> : form.provider === "openai-compatible" && availableCompatibleModels.length > 0 && !editModelId ? <div className="model-field">
            <label>
              <span>Available model</span>
              <select value={form.model} onChange={(event) => {
                if (event.target.value === "__enter_model_id__") { setEditModelId(true); return; }
                setForm((value) => ({ ...value, model: event.target.value }));
              }} required>
                {form.model && !availableCompatibleModels.includes(form.model) && <option value={form.model}>{form.model} · saved selection</option>}
                {availableCompatibleModels.map((name) => <option value={name} key={name}>{name}</option>)}
                <option value="__enter_model_id__">Enter a model ID…</option>
              </select>
            </label>
            {compatibleModels.isFetching && <span className="field-help">Loading models from this endpoint…</span>}
            {compatibleModels.isError && <span className="field-help field-help--error">{compatibleModels.error.message}</span>}
          </div> : <label>
            <span>Model identifier</span>
            <input value={form.model} onChange={(event) => setForm((value) => ({ ...value, model: event.target.value }))} spellCheck={false} required />
            {form.provider === "mlx" && mlxModels.isError && <span className="field-help field-help--error">Could not reach the MLX server.</span>}
            {form.provider === "mlx" && <button type="button" className="text-button model-refresh" disabled={mlxModels.isFetching} onClick={() => void mlxModels.refetch()}>{mlxModels.isFetching ? "Loading model…" : "Load model from endpoint"}</button>}
            {form.provider === "openai-compatible" && <>
              <button type="button" className="text-button model-refresh" disabled={compatibleModels.isFetching} onClick={() => void compatibleModels.refetch()}>{compatibleModels.isFetching ? "Loading models…" : "Load models from endpoint"}</button>
              {compatibleModels.isError && <span className="field-help field-help--error">{compatibleModels.error.message}</span>}
            </>}
            {form.provider === "openai-compatible" && availableCompatibleModels.length > 0 && <button type="button" className="text-button model-refresh" onClick={() => setEditModelId(false)}>Choose from available models</button>}
          </label>}
          {form.provider === "mlx" && mlxSupported && mlxUsesDefaultEndpoint && !mlxServerRunning && <div className="model-field">
            <button type="button" className="quiet-button" style={{ justifySelf: "start" }} disabled={startMlx.isPending || !mlxRuntime.data?.launcher_available || !selectedMlxInstalled || mlxRuntime.data?.server_status === "starting"}
              onClick={() => startMlx.mutate()}>{startMlx.isPending || mlxRuntime.data?.server_status === "starting" ? "Starting MLX…" : "Start and use MLX server"}</button>
            {!mlxRuntime.data?.launcher_available && <span className="field-help">Start the Mac companion once with <code>make mlx-host</code> in the project folder.</span>}
            {mlxRuntime.data?.launcher_available && installedMlxModels.length === 0 && <span className="field-help">No installed MLX models found in the Boundless model folder.</span>}
            {mlxRuntime.data?.launcher_available && installedMlxModels.length > 0 && !selectedMlxInstalled && <span className="field-help">Choose an installed model above to start it.</span>}
          </div>}
          {form.provider === "mlx" && mlxSupported && !mlxUsesDefaultEndpoint && mlxModels.isError && <span className="field-help">The start button manages the default MLX endpoint. Start this custom endpoint separately.</span>}
          <div className="settings-grid">
            {isLocalModel && <label>
              <span>Context window</span>
              <select value={form.context_window} onChange={(event) => setForm((value) => ({ ...value, context_window: Number(event.target.value) }))}>
                {contextChoices.map((value) => <option key={value} value={value}>{contextLabel(value)}</option>)}
                {!contextChoices.includes(form.context_window) && <option value={form.context_window}>{contextLabel(form.context_window)} · saved</option>}
              </select>
            </label>}
            <label>
              <span>Response length</span>
              <select value={form.response_length} onChange={(event) => setForm((value) => ({ ...value, response_length: event.target.value as ModelSettings["response_length"] }))}>
                <option value="concise">Concise</option><option value="standard">Standard</option>
                <option value="detailed">Detailed</option><option value="novelistic">Novelistic</option>
              </select>
            </label>
          </div>
          {supportsCreativity(form.provider, form.model) && <label className="temperature-control">
            <span>Creativity <output>{form.temperature.toFixed(2)}</output></span>
            <input type="range" min="0" max="1.5" step="0.01" value={form.temperature} onChange={(event) => setForm((value) => ({ ...value, temperature: Number(event.target.value) }))} />
            <span className="range-ends"><span>Steady</span><span>Surprising</span></span>
          </label>}
          {form.provider === "mlx" && !mlxSupported && <p className="form-message form-message--error" role="alert">MLX is supported only on Apple Silicon Macs. Choose another runtime for this device.</p>}
          <p className="form-note">{form.provider === "deepseek" ? <>DeepSeek is hosted. World prompts, enhancement requests, and campaign context are sent to its API. Your key stays in a local secret file and is never added to exports.</> : form.provider === "openai-compatible" && !isLocalModel ? <>This API endpoint is remote. Prompts, enhancements, and campaign context are sent there when you generate.</> : <>Prompts are sent to the local model endpoint shown above.</>}</p>
          {message && <p className="form-message" role="status">{message}</p>}
          <ModelRolesPanel narratorLabel={query.data ? modelDisplayLabel(query.data.provider, query.data.model) : "Active model"} />
          </div>
          <footer className="dialog-actions">
            <button type="button" className="quiet-button" onClick={closeDialog}>Close</button>
            <button type="submit" className="primary-button" disabled={save.isPending || !(form.provider === "mlx" ? selectedMlxModel : form.model).trim() || (form.provider === "mlx" && capabilities.isSuccess && !mlxSupported)}>
              {save.isPending ? <LoaderCircle className="spin" size={16} /> : <Save size={16} />}
              <span>{save.isPending ? "Saving" : "Save profile"}</span>
            </button>
          </footer>
        </form>}</>}
      </motion.section>
    </motion.div>}
    </AnimatePresence>
  );
}
