# Local state model evaluation, 2026-09-25

## Decision

Keep the saved state and summary roles on the local Qwen3.5 9B MLX narrator. The requested `richardyoung/qwen2.5-3b-instruct-abliterated:latest` Ollama model runs locally, but it did not meet the 11/12 state threshold for a recommended persistent-campaign profile. Hosted state fallback remains disabled.

## Reproducible comparison

The private Tristan regression fixture was replayed through the existing `build_interpreter_request` → Pydantic validation → entity resolution → deterministic reconciliation → CanonGuard pipeline in an isolated `boundless_test` database. All profiles received the same 20 recorded actions and final narrations (turns 11, 12, 13, 14, 17, 18, 19, 21, 22, 27, 28, 29, 30, 31, 40, 44, 45, 46, 47, 51). The benchmark re-created the campaign before each run and deleted it afterward. No new DeepSeek inference was sent.

| State interpreter | Checks | Mean state latency | Total replay time | Malformed JSON | Rejected operations | Identity result |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Qwen3.5 9B Abliterated, MLX | 11/12 | 56.6 s from saved diagnostics | 1,018.7 s | Not recorded by the baseline script | 11 | One Sable, aliases resolved |
| Qwen2.5 3B Abliterated, Ollama, existing prompt | 4/12 | 5.2 s | 105.2 s | 0 | 0 | One Sable, but former role and dark-coat alias missed |
| Qwen2.5 3B, compact prompt and Ollama JSON schema | 3/12 | 6.1 s | 122.3 s | 1 | 15 | Sable identity lost |

The baseline 9B missed the rooftop/warehouse location check and proposed five invalid IDs that reconciliation rejected. The initial 3B run missed the Ledger inventory, copied spell, player injury, location, and Sable identity details. The compact prompt experiment also proposed a false objective and removal of the established Magic theft ability. That experiment was removed from the application after the comparison.

## Live split smoke test

With a disposable campaign, the rebuilt FastAPI container routed narration to local MLX and state plus summary to local Ollama. The 3B proposed two inventory items: the real brass key and a newly copied flame spell. The spell should have become an ability, so this was a material state error despite valid JSON. A forced summary call returned unusable JSON; the existing deterministic summary fallback saved a factual summary. A deliberately unavailable Ollama model caused `/api/health` to report state and summary offline, and a turn completed without contacting hosted fallback. Restoring the installed tag recovered both health checks. The campaign was deleted and the original inherited roles restored.

The API container reached `host.docker.internal:8088/v1/models` and `host.docker.internal:11434/api/tags` with HTTP 200. With the 3B loaded at 8K context, Ollama reported 2.3–2.4 GB on GPU; system memory free was about 33% and swap in use about 14.3 GB at the measurement. A warm 9B short response took 1.3 seconds with the 3B loaded and 1.0 seconds after unloading it. A first short response took 21 seconds, so warmup and memory effects cannot be separated from that one reading. Reloading the 3B took about 2 seconds. Unloading its weights with `ollama stop` does not make an installed model unavailable because Ollama can load it again on demand; the offline-path check instead used an unavailable selected tag.

## What remains local

The restored saved roles inherit the narrator's local MLX model for state, summary, and canon repair. The mature role also uses the local MLX model, embeddings use the local hash provider, and hosted state fallback is off. The split-routing controls remain available for a stronger local bookkeeping model. Health checks report provider availability; they do not certify state accuracy.
