export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
export type GameMode = "freeform" | "guided";
export type ThemeFamily = "dark_fantasy" | "cyberpunk" | "survival" | "cozy" | "mystery" | "neutral" | "horror" | "romance" | "sci_fi" | "modern";
export type CharacterSetup = {
  character_name?: string;
  character_sex?: "male" | "female" | "intersex" | "other";
  character_gender?: "man" | "woman" | "nonbinary" | "other";
  character_pronouns?: "he/him" | "she/her" | "they/them";
  starting_money?: number;
  money_currency?: string;
};

export type Theme = {
  family: ThemeFamily;
  mood?: string;
  accent_family?: string;
  background_effect?: string;
};

export type CampaignCard = {
  id: string;
  title: string;
  protagonist_name: string;
  genre: string;
  game_mode: GameMode;
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
  suggested_actions: string[];
  status: string;
  in_world_time: string;
  created_at: string;
  attempt?: number;
  changes?: TurnChange[];
  metrics?: Record<string, number>;
};

export type TurnChange = { type: string; text: string };
export type ProfileEntry = { id: string; text: string; source: "premise" | "story" | "record" | "player" | string; turn_index: number | null;
  status: "active" | "past" | string; ended_turn_index?: number; note?: string; replaces?: string };
export type PlayerProfile = { traits: ProfileEntry[]; goals: ProfileEntry[]; history: ProfileEntry[]; world_rules: ProfileEntry[];
  reputation: ProfileEntry[]; updated_through_turn?: number };
export type SceneMood = { mood: string; intensity: number; time_of_day?: string; source?: string; turn_index?: number };

export type Branch = {
  id: string;
  campaign_id: string;
  name: string;
  parent_branch_id: string | null;
  forked_from_turn_id: string | null;
  head_turn_id: string | null;
  current_state: Record<string, unknown>;
};

export type Importance = "BACKGROUND" | "MINOR" | "RECURRING" | "MAJOR" | "COMPANION";
export type CharacterAlias = { alias: string; type: string };
export type CharacterFact = { id: string; content: string; type: string; certainty: string; provenance: string; turn_index: number | null };
export type Ability = { id: string; name: string; description: string; source: string; status: string; acquired_turn_index: number | null; limitations: string[] };
export type Character = {
  id: string; name: string; role: string; status: string; personality: string; motivations: string[];
  knowledge: Array<{ fact: string; certainty?: string; truth?: string }>; attributes: Record<string, unknown>;
  importance?: Importance; aliases?: CharacterAlias[]; facts?: CharacterFact[]; abilities?: Ability[];
  first_seen_turn_index?: number | null; last_seen_turn_index?: number | null;
};
export type Location = { id: string; name: string; region: string; description: string; aliases?: string[] };
export type Item = { id: string; name: string; quantity: number; condition: string; significance: string; aliases?: string[] };
export type Objective = {
  id: string; title: string; status: "active" | "completed" | "failed" | "abandoned" | "superseded" | string;
  description: string; aliases?: string[]; resolution_note?: string; created_turn_index?: number | null;
  completed_turn_index?: number | null; failed_turn_index?: number | null;
};
export type RelationshipEvent = {
  id: string; turn_index: number | null; turn_id: string | null; dimension: string;
  before: number | null; after: number | null; delta: number | null; reason: string; location: string;
};
export type RelationshipHistoryEntry = { reason: string; turn_index?: number; turn_id?: string; location?: string };
export type RelationshipLastInteraction = { turn_index?: number; turn_id?: string; location?: string };
export type Relationship = {
  id: string;
  from: string;
  to: string;
  from_id?: string;
  to_id?: string;
  events?: RelationshipEvent[];
  summary: string;
  dimensions: Record<string, unknown> & {
    trust?: number; respect?: number; fear?: number; hostility?: number;
    affection?: number; loyalty?: number; attraction?: number; debt?: number; dependence?: number;
    kinship?: string; awareness?: string;
    history?: RelationshipHistoryEntry[];
    last_interaction?: RelationshipLastInteraction;
  };
};
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
  game_mode: GameMode;
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
  objectives: Objective[];
  events: EventRecord[];
  memories: Array<Record<string, unknown>>;
  canon_rules: CanonRule[];
  known_secrets: Secret[];
  summary: string;
  summary_state?: { through_turn_index: number; last_attempt_turn_index: number; last_success_at: string | null; last_error: string; method: string } | null;
};

export type ModelRole = "state" | "summary" | "canon_repair" | "state_fallback";
export type ModelRoleSetting = {
  role: ModelRole; inherit: boolean; provider: ModelSettings["provider"]; base_url: string; model: string;
  temperature: number; context_window: number; hosted: boolean;
  effective: { provider: string; model: string; hosted: boolean; source_role?: string } | null;
};
export type ModelRoles = {
  narrator: Partial<ModelSettings>; narrator_hosted: boolean; roles: ModelRoleSetting[]; hosted_fallback_enabled: boolean;
};
export type RepairFinding = {
  id: string; type: string; confidence: "HIGH" | "PROBABLE" | "AMBIGUOUS"; summary: string; evidence: string[]; auto: boolean;
};
export type RepairReport = { head_turn_index: number; findings: RepairFinding[]; counts: Record<string, number> };

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

export type ImageSettings = {
  provider: "none" | "comfyui" | "ai_horde" | "perchance_assisted";
  enabled: boolean;
  base_url: string;
  checkpoint: string;
  workflow: "boundless_portrait_v1";
  width: number;
  height: number;
  steps: number;
  cfg: number;
  sampler: string;
  scheduler: string;
  auto_recurring: boolean;
  auto_major: boolean;
  auto_companion: boolean;
  auto_minor: boolean;
  allow_mature: boolean;
};

export type ImageConnection = { status: "connected" | "offline"; detail?: string; device?: string; models: string[]; queue_running?: number; queue_pending?: number };

export function portraitUrl(value: unknown): string {
  if (typeof value !== "string") return "";
  if (value.startsWith("/api/portraits/")) return `${API_URL}${value}`;
  return value.startsWith("https://") ? value : "";
}

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
  | { type: "status"; stage: string; turn_id: string }
  | { type: "complete"; turn_id: string; turn_index: number; branch_id: string; state_delta: unknown; changes?: TurnChange[]; metrics?: Record<string, number> }
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
  createCampaign: (payload: { prompt: string; game_mode: GameMode; theme_family?: ThemeFamily } & CharacterSetup) => request<CampaignDetail>("/api/campaigns", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
  setGameMode: (id: string, branchId: string, gameMode: GameMode) => request<CampaignDetail>(`/api/campaigns/${id}/game-mode?branch_id=${branchId}`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ game_mode: gameMode }),
  }),
  setTheme: (id: string, branchId: string, themeFamily: ThemeFamily) => request<CampaignDetail>(`/api/campaigns/${id}/theme?branch_id=${branchId}`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ theme_family: themeFamily }),
  }),
  refreshSetup: (id: string, branchId: string) => request<CampaignDetail>(`/api/campaigns/${id}/refresh-setup?branch_id=${branchId}`, { method: "POST" }),
  refreshStory: (id: string, branchId: string) => request<{ changes: TurnChange[]; campaign: CampaignDetail }>(`/api/campaigns/${id}/refresh-story?branch_id=${branchId}`, { method: "POST" }),
  reindexPeople: (id: string, branchId: string) => request<CampaignDetail>(`/api/campaigns/${id}/reindex-people?branch_id=${branchId}`, { method: "POST" }),
  generateAvatar: (campaignId: string, branchId: string, characterId: string, newSeed = false) =>
    request<{ done: boolean; job_id: string; status: string }>(`/api/campaigns/${campaignId}/characters/${characterId}/avatar?branch_id=${branchId}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ new_seed: newSeed }) }),
  avatarStatus: (campaignId: string, branchId: string, characterId: string) =>
    request<{ done: boolean; job_id: string; status: string; error?: string; avatar_url?: string }>(`/api/campaigns/${campaignId}/characters/${characterId}/avatar?branch_id=${branchId}`),
  removeAvatar: (campaignId: string, branchId: string, characterId: string) =>
    request<{ removed: boolean }>(`/api/campaigns/${campaignId}/characters/${characterId}/avatar?branch_id=${branchId}`, { method: "DELETE" }),
  uploadAvatar: (campaignId: string, branchId: string, characterId: string, image: File) => {
    const body = new FormData(); body.append("image", image);
    return request<{ done: boolean; avatar_url: string }>(`/api/campaigns/${campaignId}/characters/${characterId}/avatar/upload?branch_id=${branchId}`, { method: "POST", body });
  },
  updateCharacter: (campaignId: string, branchId: string, characterId: string, payload: {
    role: string; personality: string; appearance: string; sex: string; gender: string; pronouns: string;
  }) => request<CampaignDetail>(`/api/campaigns/${campaignId}/characters/${characterId}?branch_id=${branchId}`, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  }),
  updateRelationship: (campaignId: string, branchId: string, relationshipId: string, payload: {
    trust: number | null; respect: number | null; fear: number | null; hostility: number | null; status: string; summary: string;
  }) => request<CampaignDetail>(`/api/campaigns/${campaignId}/relationships/${relationshipId}?branch_id=${branchId}`, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  }),
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
  imageSettings: () => request<ImageSettings>("/api/settings/images"),
  saveImageSettings: (settings: ImageSettings) => request<ImageSettings>("/api/settings/images", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(settings) }),
  testImageConnection: (baseUrl: string) => request<ImageConnection>(`/api/settings/images/test?base_url=${encodeURIComponent(baseUrl)}`),
  mlxRuntime: () => request<MlxRuntime>("/api/settings/mlx/runtime"),
  startMlx: (model: string) => request<{ server_status: string; detail: string }>("/api/settings/mlx/start", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ model }),
  }),
  ollamaModels: (baseUrl: string) => request<{ models: string[] }>(`/api/settings/ollama/models?base_url=${encodeURIComponent(baseUrl)}`),
  compatibleModels: (baseUrl: string) => request<{ models: string[] }>(`/api/settings/openai-compatible/models?base_url=${encodeURIComponent(baseUrl)}`),
  deepseekModels: (apiKey?: string) => request<{ models: string[] }>("/api/settings/deepseek/models", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ api_key: apiKey || undefined }) }),
  modelRoles: () => request<ModelRoles>("/api/settings/roles"),
  saveModelRoles: (payload: { roles: Array<Partial<ModelRoleSetting> & { role: ModelRole; inherit: boolean; api_key?: string }>; hosted_fallback_enabled: boolean }) =>
    request<ModelRoles>("/api/settings/roles", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
  repairReport: (id: string, branchId: string) => request<RepairReport>(`/api/campaigns/${id}/repair?branch_id=${branchId}`),
  applyRepair: (id: string, branchId: string, findingIds: string[], includeHigh: boolean) =>
    request<{ result: { applied: string[] }; campaign: CampaignDetail }>(`/api/campaigns/${id}/repair?branch_id=${branchId}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ finding_ids: findingIds, include_high_confidence: includeHigh }),
    }),
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
