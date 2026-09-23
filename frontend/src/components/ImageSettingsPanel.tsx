"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, ImageSettings } from "@/lib/api";

const defaultSettings: ImageSettings = {
  provider: "none", enabled: false, base_url: "", checkpoint: "", workflow: "boundless_portrait_v1",
  width: 768, height: 1024, steps: 28, cfg: 6.5, sampler: "dpmpp_2m", scheduler: "karras",
  auto_recurring: true, auto_major: true, auto_companion: true, auto_minor: false,
};

export function ImageSettingsPanel() {
  const client = useQueryClient();
  const settings = useQuery({ queryKey: ["image-settings"], queryFn: api.imageSettings });
  const [draft, setDraft] = useState<ImageSettings | null>(null);
  const [message, setMessage] = useState("");
  const form = draft ?? settings.data ?? defaultSettings;
  const update = (changes: Partial<ImageSettings>) => setDraft({ ...form, ...changes });
  const connection = useQuery({ queryKey: ["image-connection", form.base_url], queryFn: () => api.testImageConnection(form.base_url), enabled: false, retry: false });
  const save = useMutation({
    mutationFn: () => api.saveImageSettings(form),
    onSuccess: async (value) => { setDraft(value); setMessage("Portrait settings saved."); await client.invalidateQueries({ queryKey: ["image-settings"] }); },
    onError: (error) => setMessage(error.message),
  });
  const models = connection.data?.status === "connected" ? connection.data.models : [];
  return <form className="settings-form image-settings-form" onSubmit={(event) => { event.preventDefault(); save.mutate(); }}>
    <div className="settings-fields">
      <p className="form-note">Portraits are optional. Story generation continues when an image server is offline.</p>
      <label className="image-enable"><input type="checkbox" checked={form.enabled} onChange={(event) => update({ enabled: event.target.checked })} disabled={form.provider === "none"} /><span>Enable portrait generation</span></label>
      <label><span>Image provider</span><select value={form.provider} onChange={(event) => update({ provider: event.target.value as ImageSettings["provider"], enabled: event.target.value !== "none" && form.enabled })}>
        <option value="none">None</option><option value="comfyui">ComfyUI · local or private network</option>
        <option value="ai_horde">AI Horde · community cloud</option><option value="perchance_assisted">Perchance Assisted · manual upload</option>
      </select></label>
      {form.provider === "comfyui" && <>
        <label><span>ComfyUI endpoint</span><input type="url" placeholder="http://100.x.x.x:8188" value={form.base_url} onChange={(event) => update({ base_url: event.target.value })} spellCheck={false} required={form.enabled} /></label>
        <div className="image-connection-row"><button type="button" className="quiet-button" disabled={!/^https?:\/\//.test(form.base_url) || connection.isFetching} onClick={() => void connection.refetch()}>{connection.isFetching ? "Checking…" : "Test connection"}</button>
          <span role="status">{connection.isFetching ? "Connecting" : connection.data?.status === "connected" ? `Connected${connection.data.device ? ` · ${connection.data.device}` : ""}` : connection.data?.status === "offline" ? "ComfyUI offline" : "Not checked"}</span></div>
        {connection.data?.status === "offline" && <p className="field-help field-help--error">{connection.data.detail}</p>}
        {connection.data?.status === "connected" && <p className="field-help">Queue: {connection.data.queue_running ?? 0} running, {connection.data.queue_pending ?? 0} waiting.</p>}
        <label><span>Checkpoint</span>{models.length > 0 ? <select value={form.checkpoint} onChange={(event) => update({ checkpoint: event.target.value })} required={form.enabled}>
          <option value="">Choose an installed checkpoint</option>{form.checkpoint && !models.includes(form.checkpoint) && <option value={form.checkpoint}>{form.checkpoint} · saved</option>}
          {models.map((name) => <option key={name} value={name}>{name}</option>)}
        </select> : <input value={form.checkpoint} onChange={(event) => update({ checkpoint: event.target.value })} placeholder="Test connection to load installed checkpoints" required={form.enabled} />}</label>
        <label><span>Workflow</span><select value={form.workflow} onChange={(event) => update({ workflow: event.target.value as ImageSettings["workflow"] })}><option value="boundless_portrait_v1">Boundless portrait · SDXL</option></select></label>
        <div className="settings-grid"><label><span>Portrait size</span><select value={`${form.width}x${form.height}`} onChange={(event) => { const [width, height] = event.target.value.split("x").map(Number); update({ width, height }); }}><option value="768x1024">768 × 1024</option><option value="832x1216">832 × 1216</option><option value="512x768">512 × 768</option></select></label>
          <label><span>Steps</span><select value={form.steps} onChange={(event) => update({ steps: Number(event.target.value) })}><option value={20}>20 · faster</option><option value={28}>28 · balanced</option><option value={36}>36 · detailed</option></select></label></div>
        <div className="settings-grid"><label><span>Guidance</span><select value={form.cfg} onChange={(event) => update({ cfg: Number(event.target.value) })}><option value={5}>5 · subtle</option><option value={6.5}>6.5 · balanced</option><option value={8}>8 · strong</option></select></label>
          <label><span>Sampler</span><select value={form.sampler} onChange={(event) => update({ sampler: event.target.value })}><option value="dpmpp_2m">DPM++ 2M</option><option value="euler">Euler</option></select></label></div>
      </>}
      {form.provider === "perchance_assisted" && <div className="image-assisted"><p>Generate an image in Perchance, save it, then use Change portrait on a character to upload it to Boundless.</p><a href="https://perchance.org/ai-text-to-image-generator" target="_blank" rel="noopener noreferrer">Open Perchance image generator ↗</a></div>}
      {form.provider === "ai_horde" && <p className="form-note">AI Horde sends character appearance and art direction to a community service. Generated images are copied into Boundless storage.</p>}
      {(form.provider === "comfyui" || form.provider === "ai_horde") && <fieldset className="image-auto-options"><legend>Automatic portraits after story turns</legend>
        {([ ["auto_companion", "Companions"], ["auto_major", "Major characters"], ["auto_recurring", "Recurring characters"], ["auto_minor", "Minor characters"] ] as const).map(([key, label]) => <label key={key}><input type="checkbox" checked={form[key]} onChange={(event) => update({ [key]: event.target.checked })} /><span>{label}</span></label>)}
        <p>Background people are always manual. Automatic generation runs after the story is saved.</p>
      </fieldset>}
      {message && <p className="form-message" role="status">{message}</p>}
    </div>
    <footer className="dialog-actions"><button type="submit" className="primary-button" disabled={save.isPending || (form.enabled && form.provider === "comfyui" && (!form.base_url || !form.checkpoint))}>{save.isPending ? "Saving…" : "Save portrait settings"}</button></footer>
  </form>;
}
