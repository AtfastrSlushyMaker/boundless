# Boundless

**Create any world. Be anyone. Do anything.**

Boundless is a local-first text role-playing game. Describe a world and a character in ordinary language, then act freely. An AI Game Master narrates the consequences while the campaign stores its rules, people, places, inventory, memories, and alternate timelines in PostgreSQL.

## What you can do

- Create or enhance a world prompt, then play by writing your own actions or choosing model-suggested moves.
- Establish hard campaign rules, including mortality and exceptions, that the Game Master must respect.
- Stream story turns and keep characters, relationships, inventory, and discovered lore in a persistent campaign.
- Edit or regenerate narration, rewind to a checkpoint, or branch into another timeline.
- Rename, archive, duplicate, export, and import campaigns.
- Use a native Apple Silicon MLX server, Ollama, an OpenAI-compatible endpoint, or hosted DeepSeek. Model selection and health are available in Settings.

At world creation, **How do you want to play?** offers **Write every action** and **Get choices after each scene**. In choice mode, the Game Master suggests up to three actions after each completed scene. Click one to attempt it, or type anything in **Your next action**. The **Play style** select in the game lets you switch modes later. Choices are saved with their turns, so they remain available after refreshing or branching. If a model cannot produce usable choices, freeform input remains available.

**World mood** chooses the campaign's colors and map treatment from the premise, or lets you pick a look. You can change it beside Play style during a game. **Character details** in world creation optionally records a name, sex, gender, pronouns, and starting money; leave them blank when the premise already says enough. The character panel shows known details and offers **Refresh details from premise** for older campaigns. The refresh fills missing setup facts and replays previously recorded character and location changes without replacing established facts.

The world record keeps a compact memory of each completed turn even when a local model emits no structured facts. Characters, locations, and possessions are updated from supported state changes, while the narrator is instructed to react to each action and move the scene forward. Explicit player corrections about their own identity take precedence over the narrator's assumptions.

The **People** panel shows known roles, identity details, motives, and established family links. **View connections** opens a scrollable, zoomable graph of every visible person and recorded relationship. You can correct a character's role, appearance, identity, and personality or the four relationship scores and status while the story continues. **Recover people from story** scans earlier turns for explicitly named or individually encountered people when a local model omitted them. Parent names are linked automatically when the premise or narration states them; the app leaves an unstated relative's name unknown. Before a new turn is shown, Boundless checks for obvious unchosen player actions and deaths that skip the player's chance to respond to lethal danger.

**Portrait generation** is optional. Settings offers a ComfyUI server, [AI Horde's community API](https://github.com/Haidra-Org/AI-Horde/blob/main/README_integration.md), Perchance Assisted, or no provider. ComfyUI and AI Horde jobs run after story persistence in a durable queue. Boundless stores resulting images in its own portrait volume. You can also upload a saved image to any character. Perchance Assisted opens the [Perchance image generator](https://perchance.org/ai-text-to-image-generator) for manual creation and upload. The [Perchance API tutorial](https://perchance.org/api-tutorial) describes a text-list example endpoint, not an image API; its sample URL currently returns `Cannot GET`, so automatic image generation does not depend on it.

## Quick start with Docker Compose

This starts PostgreSQL, runs database migrations, and builds the API and web images. Docker Engine with Compose is required. On Apple Silicon Macs, the MLX start button also needs Python 3.11+ and the installed `mlx_lm.server` command. The images do not contain a language model.

```bash
cp .env.example .env
make stack-up
docker compose ps
```

Open [http://localhost:3000](http://localhost:3000). Before creating a campaign, open **Model settings** and connect one of the providers below. The API readiness endpoint is [http://127.0.0.1:8000/api/ready](http://127.0.0.1:8000/api/ready); [health](http://127.0.0.1:8000/api/health) also reports model availability.

The Compose default is Ollama at `http://host.docker.internal:11434` with model `qwen3.5:9b`. This is only a starting configuration. The model must be installed and the server must be reachable from the API container. For a quick hosted trial, choose **DeepSeek · hosted** in Settings and enter your API key there. The default model ID `deepseek-flash` serves DeepSeek V4.1 Flash, and the dropdown loads models from DeepSeek's `/models` API. [DeepSeek model details](https://api-docs.deepseek.com/quick_start/pricing/)

```bash
docker compose logs -f api web
docker compose down
```

`make stack-up` runs Compose and, on Apple Silicon Macs, starts a small host companion so the app can launch MLX on demand. If you run `docker compose up -d --build --wait` directly, start the companion separately with `make mlx-host` and set `HOST_MLX_SUPPORTED=true` in `.env`. `make stack-logs` and `make stack-down` manage the containers. `make db-up` starts only PostgreSQL for native development.

`docker compose down` keeps the PostgreSQL, API secret, and portrait volumes. `docker compose down -v` deletes all three volumes and their data. The API, web app, and database publish only to `127.0.0.1` on the host.

## Optional portrait server

Boundless on the Mac can use its MLX model for text while a Windows desktop runs ComfyUI for portraits. FastAPI communicates with ComfyUI over a Tailscale or trusted LAN URL. The endpoint is saved in **Settings → Portrait generation**; it is not built into the source code. **Test connection** runs from FastAPI, so it checks the path the backend actually uses, including when FastAPI runs in Docker.

```text
MacBook: Next.js + FastAPI + PostgreSQL + MLX text
                        │
                 Tailscale or LAN
                        │
Windows desktop: ComfyUI + NVIDIA GPU, port 8188
```

The configured Windows desktop uses its existing official ComfyUI checkout at `C:\Users\malek\ComfyUI`, CUDA virtual environment, and `juggernautXL_Ragnarok.safetensors` checkpoint (about 6.62 GB). No second installation or checkpoint download was needed. A Windows Scheduled Task named `Boundless ComfyUI` starts ComfyUI at logon. It listens only on the desktop's Tailscale IPv4 address on port 8188. The `Boundless ComfyUI (Tailscale only)` firewall rule is limited to that local address, the Tailscale interface, the Private profile, and remote Tailscale peers (`100.64.0.0/10`). LAN access is not enabled on this desktop. Other users can configure their own trusted LAN or Tailscale endpoint in Settings; do not expose ComfyUI to the public internet.

From PowerShell on that Windows desktop, the helpers in `C:\Users\malek\ComfyUI\boundless` are:

```powershell
& "$env:USERPROFILE\ComfyUI\boundless\Start-ComfyUI.ps1"
& "$env:USERPROFILE\ComfyUI\boundless\Stop-ComfyUI.ps1"
& "$env:USERPROFILE\ComfyUI\boundless\Restart-ComfyUI.ps1"
& "$env:USERPROFILE\ComfyUI\boundless\View-ComfyUI-Logs.ps1" -Tail 100
tailscale ip -4
```

Enter `http://<desktop-tailscale-ip>:8188` in Settings, or another URL reachable from **FastAPI** for a different ComfyUI host. The test button discovers installed checkpoints and reports the GPU and queue state. Add another `.safetensors` checkpoint to the ComfyUI `models/checkpoints` directory, then test the connection again to select it. The current checkpoint and endpoint live in Boundless settings, not application code. The Windows task and firewall rule can be removed independently of Boundless.

Once the server is running, select **ComfyUI**, enter its reachable URL, click **Test connection**, choose a checkpoint from the discovered list, then save. The default workflow is [boundless_portrait_v1.json](backend/app/workflows/boundless_portrait_v1.json): an SDXL checkpoint loader, positive and negative prompts, 768 × 1024 latent, KSampler, VAE decode, and SaveImage. Size, steps, guidance, sampler, and checkpoint are configurable. The positive prompt uses the character's structured visual identity, current appearance, role, faction, and campaign mood. It does not send a full transcript. Stable seeds keep regeneration consistent; **New seed** deliberately changes the seed.

Generated images are copied into `data/portraits/{campaign_id}/{character_id}/{portrait_id}.{png,jpg,webp}` for native development, or the Docker `api_portraits` volume. Compose initializes this volume for the non-root API user before startup. The database stores provider, prompt, model, seed, parameters, status, and relative image path. Jobs can be queued, generating, complete, failed, or cancelled. Offline servers back off without blocking story turns. If you want to avoid sending character details to a cloud service, leave AI Horde disabled and do not use it as a fallback. Perchance Assisted is a manual save-and-upload flow.

## Model options

| Provider                             | Where it runs                 | Endpoint to enter in Settings                                                   |
| ------------------------------------ | ----------------------------- | ------------------------------------------------------------------------------- |
| DeepSeek                             | Hosted API                    | `https://api.deepseek.com`                                                      |
| Ollama, native app                   | Your computer                 | `http://127.0.0.1:11434`                                                        |
| Ollama, Docker API                   | Your computer                 | `http://host.docker.internal:11434`                                             |
| OpenAI-compatible server, native app | Your computer or another host | The server's `/v1` base URL                                                     |
| OpenAI-compatible server, Docker API | A reachable host              | A URL reachable from the container, often `http://host.docker.internal:8080/v1` |
| MLX, native API                      | Apple Silicon Mac             | `http://127.0.0.1:8088/v1`                                                      |
| MLX, Docker API                      | Apple Silicon Mac host        | `http://host.docker.internal:8088/v1`                                           |

For Ollama, install and start it on the host, then download a model such as [`qwen3.5:9b`](https://ollama.com/library/qwen3.5):

```bash
ollama pull qwen3.5:9b
```

Ollama's model dropdown reads installed names from `/api/tags`. If the Docker API cannot reach the host server, configure Ollama to listen on a host interface reachable through `host.docker.internal`, and restrict that listener to trusted local networks. On Linux, Docker maps `host.docker.internal` through `host-gateway`; a process listening only on host loopback may not be reachable from the container. [Docker host networking reference](https://docs.docker.com/compose/how-tos/networking/)

For `llama.cpp`, start its OpenAI-compatible HTTP server with a model you already have, then choose **OpenAI-compatible API** in Settings. Use a loopback `/v1` URL when both Boundless and the model server run natively; use a container-reachable host URL for the Docker API. Boundless loads available model IDs from the server's `/models` endpoint. [llama.cpp server documentation](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)

MLX inference still runs on the Apple Silicon host when Boundless runs in Docker. `make stack-up` starts the loopback-only host companion and enables MLX in the Docker API. In Model settings, choose **Apple Silicon · MLX**, select a downloaded model, then click **Start and use MLX server**. The companion lists models in `~/.local/share/boundless/models`, launches `mlx_lm.server` with the selected path, and the app also reads `/v1/models` from a running server. The server may take time to load the weights. On other hosts, the MLX choice stays unavailable.

```bash
mlx_lm.server --model ~/.local/share/boundless/models/qwen3.5-9b-abliterated-mlx-4bit --host 127.0.0.1 --port 8088
```

The companion listens only on Mac loopback port 8091 and accepts start requests from the Boundless API. It only starts models found in the local model folder; it never downloads a model. It writes diagnostic logs to ignored `.mlx-host.log` and `.mlx-server.log` files. If you prefer to run the MLX server yourself, use the command above. A successful `mlx_lm.generate` command confirms the weights work, but the app needs the HTTP server running. If Docker cannot reach a server bound to Mac loopback, bind the server to an interface reachable by Docker and keep that listener on a trusted local network.

### Native development and Apple MLX

Native development needs Node.js 20.9+, npm, Python 3.11+, [uv](https://docs.astral.sh/uv/), and Docker for PostgreSQL. For the recommended MLX model, use Apple Silicon with enough free memory and disk space for its weights. On Windows and Linux, choose Ollama or another compatible provider instead.

From the repository root:

```bash
cp .env.example .env
make db-up
cd backend
uv sync --extra dev
uv run alembic upgrade head
```

On Apple Silicon, install the optional MLX dependency and start the model server in a separate terminal:

```bash
cd backend
uv sync --extra mlx
uv run mlx_lm.server --model lukey03/Qwen3.5-9B-abliterated-MLX-4bit --host 127.0.0.1 --port 8088
```

Run the API and web app in separate terminals:

```bash
cd backend
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

```bash
cd frontend
npm ci
npm run dev
```

The native `.env.example` defaults to the MLX model. Change `LLM_PROVIDER`, `LLM_BASE_URL`, and `LLM_MODEL` in your ignored `.env`, or use Model settings after startup. The model profile saved in PostgreSQL takes precedence over those startup defaults.

## Configuration and data

Copy [.env.example](.env.example) for local configuration. Docker Compose uses `COMPOSE_LLM_PROVIDER`, `COMPOSE_LLM_BASE_URL`, and `COMPOSE_LLM_MODEL` so its Linux API can default to Ollama while native macOS development can default to MLX. `make stack-up` sets `HOST_MLX_SUPPORTED=true` automatically on Apple Silicon Macs. Direct Compose users can set it in `.env`; `MLX_HOST_BASE_URL` and `COMPOSE_MLX_LAUNCHER_BASE_URL` control the Docker-reachable native endpoints. `APP_PORT`, `FRONTEND_PORT`, and `POSTGRES_PORT` control host ports. Changing `APP_PORT` requires rebuilding the web image because the browser API URL is compiled into the Next.js build.

Database records live in the Compose `postgres_data` volume. A DeepSeek key entered through Docker Model settings is saved with owner-only permissions in the `api_secrets` volume. A key entered through the native API is saved in the ignored `.secrets/deepseek_api_key` file. These are separate stores, so enter the key once in each environment you use. Keys are not included in campaign exports. A hosted provider receives the campaign context required for generation; a local provider keeps generation on the configured local endpoint.

The web and API ports are bound to host loopback. Boundless currently has no account system, so keep it on a trusted machine and avoid publishing those ports to the internet.

## How campaigns work

1. Boundless stores the original world prompt and derives a Campaign Constitution. Explicit hard rules are kept separately from summaries.
2. The context builder selects the current state, relevant lore and memories, summaries, and recent turns within a model budget.
3. The selected provider generates narration. CanonGuard checks hard rules, player agency, and sudden player death before the turn is shown, and asks the model for a repair when needed.
4. A state interpretation pass writes validated changes to characters, items, relationships, events, and other campaign records. A conservative people index fills explicit encounters that a local model missed, then the turn creates a checkpoint.
5. Editing, regenerating, rewinding, and branching use checkpoints and response versions so each timeline has its own state.

Hidden canon marked `GM_ONLY` is not shown in player lore panels. See [architecture](docs/architecture.md) for the components and data flow.

## Campaign files and controls

The archive supports rename, archive, restore, duplicate, export, import, and delete. Exports use versioned `.boundless.json` files and include branches, turns, checkpoints, world state, hidden GM facts, and model profile metadata without credentials. Treat exported campaign files as private. Import validates the file before writing it to PostgreSQL. In the action field, `/canon`, `/retcon`, and `/ooc` make intentional out-of-character changes.

For an extra backup, export campaigns before resetting a database volume. Exports are portable campaign data, while a PostgreSQL volume preserves the full local installation.

## Development checks

```bash
docker compose config --quiet
cd backend && uv run ruff check .
cd backend && uv run pytest
cd frontend && npm run lint
cd frontend && npm run build
```

The integration tests use a separate `boundless_test` database (created and migrated by `make test`) and never touch your campaigns. `make test` and `make lint` run the backend tests and both linters from the repository root. To rebuild just the app images, run `docker compose build api web`.

## Troubleshooting

| Symptom                                        | Check                                                                                                                                                                       |
| ---------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `api` will not start                           | Run `docker compose ps` and `docker compose logs migrate api`. The migration step waits for a healthy database.                                                             |
| Port already in use                            | Set `APP_PORT`, `FRONTEND_PORT`, or `POSTGRES_PORT` in `.env`; rebuild the web image after changing `APP_PORT`.                                                             |
| Model offline                                  | Check the endpoint in Model settings from the API's point of view. `127.0.0.1` inside a container refers to that container, not the host.                                   |
| MLX unavailable in Docker                      | On an Apple Silicon Mac, run `make stack-up`. For direct Compose use, set `HOST_MLX_SUPPORTED=true` in `.env` and recreate the API. On other hosts MLX remains unavailable. |
| MLX selected but offline                       | Select a downloaded model and click **Start and use MLX server**. If the Mac companion is offline, run `make mlx-host` and retry.                                           |
| DeepSeek key missing after switching to Docker | Re-enter the key in Docker Model settings; its named volume is separate from the native `.secrets` folder.                                                                  |
| Campaigns seem missing                         | Confirm that the same Compose project and `postgres_data` volume are in use. `docker compose down` retains it; `down -v` removes it.                                        |

## Contributing and license

The repository has a Next.js frontend, FastAPI backend, Alembic migrations, and a PostgreSQL 18 database with pgvector. Keep provider integrations in `backend/app/llm/`, campaign rules in `backend/app/services/`, and browser API calls in `frontend/src/lib/api.ts`. Run the relevant checks above and keep secrets, model weights, databases, and generated runtime files out of Git. The root [.gitignore](.gitignore) and both `.dockerignore` files exclude local artifacts from commits and image build contexts.

A license has not yet been selected or added to this repository. Choose one before treating the source as an open-source release.
