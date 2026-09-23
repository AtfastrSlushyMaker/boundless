export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

export type Theme = {
  family: "dark_fantasy" | "cyberpunk" | "survival" | "cozy" | "mystery" | "neutral";
  mood?: string;
  accent_family?: string;
  background_effect?: string;
};

export type CampaignCard = {
  id: string;
  title: string;
  protagonist_name: string;
  genre: string;
  premise: string;
  current_location: string;
  turn_count: number;
  last_played: string;
  theme: Theme;
  archived: boolean;
  branch_id: string | null;
};

export type Turn = {
  id: string;
  branch_id: string;
  parent_turn_id: string | null;
  turn_index: number;
  player_action: string;
  gm_response: string;
  status: string;
  in_world_time: string;
  created_at: string;
};

export type Branch = {
  id: string;
  campaign_id: string;
  name: string;
  parent_branch_id: string | null;
  forked_from_turn_id: string | null;
  head_turn_id: string | null;
  current_state: Record<string, unknown>;
};

export type Character = { id: string; name: string; role: string; status: string; personality: string };
export type Location = { id: string; name: string; region: string; description: string };
export type Item = { id: string; name: string; quantity: number; condition: string; significance: string };
export type Relationship = { id: string; to: string; summary: string; dimensions: Record<string, string | number> };
export type EventRecord = { id: string; content: string; certainty: string };
export type CanonRule = { id: string; statement: string };
export type Secret = { id: string; name: string; content: string };

export type CampaignDetail = {
  id: string;
  title: string;
  original_prompt: string;
  constitution: Record<string, unknown>;
  theme: Theme;
  protagonist_name: string;
  genre: string;
  archived: boolean;
  active_branch_id: string;
  branch: Branch;
  branches: Branch[];
  turns: Turn[];
  current_state: Record<string, unknown>;
  current_location: string;
  characters: Character[];
  locations: Location[];
  factions: Array<Record<string, unknown>>;
  items: Item[];
  inventory: Item[];
  relationships: Relationship[];
  objectives: Array<Record<string, unknown>>;
  events: EventRecord[];
  memories: Array<Record<string, unknown>>;
  canon_rules: CanonRule[];
  known_secrets: Secret[];
  summary: string;
};

export type ModelSettings = {
  provider: "mlx" | "openai-compatible" | "ollama" | "deepseek";
  model: string;
  base_url: string;
  context_window: number;
  response_length: "concise" | "standard" | "detailed" | "novelistic";
  temperature: number;
  health?: { status: string; detail?: string; available_models?: string[] };
  api_key_configured?: boolean;
};

export type SystemCapabilities = { mlx_supported: boolean; mlx_default_base_url: string };
export type MlxRuntime = {
  launcher_available: boolean;
  server_status: "running" | "starting" | "offline" | "unknown";
  models: Array<{ id: string; name: string }>;
  selected_model?: string | null;
  detail?: string;
};

export type StreamEvent =
  | { type: "delta"; text: string; turn_id: string }
  | { type: "replace"; text: string; turn_id: string; reason?: string }
  | { type: "complete"; turn_id: string; turn_index: number; branch_id: string; state_delta: unknown }
  | { type: "error"; turn_id?: string; message: string };

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, { ...init, cache: "no-store" });
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      detail = body.detail ?? detail;
    } catch { /* response body was not JSON */ }
    throw new Error(detail);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  health: () => request<{ status: string; database: string; model: { status: string; model?: string; selection?: string } }>("/api/health"),
  capabilities: () => request<SystemCapabilities>("/api/system/capabilities"),
  campaigns: (archived = false) => request<CampaignCard[]>(`/api/campaigns?include_archived=${archived}`),
  campaign: (id: string, branchId?: string) => request<CampaignDetail>(`/api/campaigns/${id}${branchId ? `?branch_id=${branchId}` : ""}`),
  createCampaign: (prompt: string) => request<CampaignDetail>("/api/campaigns", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ prompt }) }),
  enhanceWorld: (prompt: string, direction: string) => request<{ prompt: string }>("/api/campaigns/enhance", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ prompt, direction }) }),
  renameCampaign: (id: string, title: string) => request(`/api/campaigns/${id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title }) }),
  archiveCampaign: (id: string, archived: boolean) => request(`/api/campaigns/${id}/archive?archived=${archived}`, { method: "POST" }),
  duplicateCampaign: (id: string) => request<CampaignDetail>(`/api/campaigns/${id}/duplicate`, { method: "POST" }),
  deleteCampaign: (id: string) => request<void>(`/api/campaigns/${id}`, { method: "DELETE" }),
  createBranch: (id: string, name: string, branchId: string, turnId?: string) => request<{ id: string; name: string }>(`/api/campaigns/${id}/branches?branch_id=${branchId}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, turn_id: turnId }) }),
  activateBranch: (id: string, branchId: string) => request(`/api/campaigns/${id}/branches/${branchId}/activate`, { method: "POST" }),
  rewind: (id: string, branchId: string, turnId: string) => request<CampaignDetail>(`/api/campaigns/${id}/rewind?branch_id=${branchId}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ turn_id: turnId }) }),
  editTurn: (turnId: string, content: string) => request<CampaignDetail>(`/api/turns/${turnId}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ content }) }),
  settings: () => request<ModelSettings>("/api/settings/model"),
  mlxRuntime: () => request<MlxRuntime>("/api/settings/mlx/runtime"),
  startMlx: (model: string) => request<{ server_status: string; detail: string }>("/api/settings/mlx/start", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ model }),
  }),
  ollamaModels: (baseUrl: string) => request<{ models: string[] }>(`/api/settings/ollama/models?base_url=${encodeURIComponent(baseUrl)}`),
  compatibleModels: (baseUrl: string) => request<{ models: string[] }>(`/api/settings/openai-compatible/models?base_url=${encodeURIComponent(baseUrl)}`),
  deepseekModels: (apiKey?: string) => request<{ models: string[] }>("/api/settings/deepseek/models", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ api_key: apiKey || undefined }) }),
  saveSettings: (settings: ModelSettings & { api_key?: string }) => request<ModelSettings>("/api/settings/model", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(settings) }),
};

export async function streamTurn(
  campaignId: string,
  payload: { action: string; branch_id: string; instruction?: string },
  onEvent: (event: StreamEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  const response = await fetch(`${API_URL}/api/campaigns/${campaignId}/turns/stream`, {
    method: "POST", headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(payload), signal,
  });
  if (!response.ok || !response.body) {
    let message = `Could not start the turn (${response.status})`;
    try { message = (await response.json()).detail ?? message; } catch { /* leave fallback */ }
    throw new Error(message);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    const frames = buffer.split(/\r?\n\r?\n/);
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      const data = frame.split(/\r?\n/).filter((line) => line.startsWith("data:")).map((line) => line.slice(5).trim()).join("\n");
      if (!data || data === "[DONE]") continue;
      try { onEvent(JSON.parse(data) as StreamEvent); } catch { /* Ignore malformed transport frames. */ }
    }
    if (done) break;
  }
}

export async function exportCampaign(id: string, title: string): Promise<void> {
  const response = await fetch(`${API_URL}/api/campaigns/${id}/export`);
  if (!response.ok) throw new Error("Campaign export failed.");
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `${title.trim().replace(/[^a-z0-9_-]+/gi, "-").replace(/^-|-$/g, "") || "campaign"}.boundless.json`;
  anchor.click();
  URL.revokeObjectURL(url);
}

export async function importCampaign(file: File): Promise<CampaignDetail> {
  const form = new FormData();
  form.append("file", file);
  return request<CampaignDetail>("/api/campaigns/import", { method: "POST", body: form });
}
