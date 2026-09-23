# Boundless

**Create any world. Be anyone. Do anything.**

Boundless is a local-first text role-playing game. Describe a world and a character in ordinary language, then act freely. An AI Game Master narrates the consequences while the campaign stores its rules, people, places, inventory, memories, and alternate timelines in PostgreSQL.

## What you can do

- Create or enhance a world prompt, then play without fixed dialogue choices.
- Establish hard campaign rules, including mortality and exceptions, that the Game Master must respect.
- Stream story turns and keep characters, relationships, inventory, and discovered lore in a persistent campaign.
- Edit or regenerate narration, rewind to a checkpoint, or branch into another timeline.
- Rename, archive, duplicate, export, and import campaigns.
- Use a native Apple Silicon MLX server, Ollama, an OpenAI-compatible endpoint, or hosted DeepSeek. Model selection and health are available in Settings.

## Quick start with Docker Compose

This starts PostgreSQL, runs database migrations, and builds the API and web images. Docker Engine with Compose is required. The images do not contain a language model.

```bash
cp .env.example .env
docker compose up -d --build --wait
docker compose ps
```

Open [http://localhost:3000](http://localhost:3000). Before creating a campaign, open **Model settings** and connect one of the providers below. The API readiness endpoint is [http://127.0.0.1:8000/api/ready](http://127.0.0.1:8000/api/ready); [health](http://127.0.0.1:8000/api/health) also reports model availability.

The Compose default is Ollama at `http://host.docker.internal:11434` with model `qwen3.5:9b`. This is only a starting configuration. The model must be installed and the server must be reachable from the API container. For a quick hosted trial, choose **DeepSeek · hosted** in Settings and enter your API key there. The default model ID `deepseek-flash` serves DeepSeek V4.1 Flash, and the dropdown loads models from DeepSeek's `/models` API. [DeepSeek model details](https://api-docs.deepseek.com/quick_start/pricing/)

```bash
docker compose logs -f api web
docker compose down
```

`make stack-up`, `make stack-logs`, and `make stack-down` wrap these Compose commands. `make db-up` starts only PostgreSQL for native development.

`docker compose down` keeps the PostgreSQL and API secret volumes. `docker compose down -v` deletes both volumes and their data. The API, web app, and database publish only to `127.0.0.1` on the host.

## Model options

| Provider | Where it runs | Endpoint to enter in Settings |
| --- | --- | --- |
| DeepSeek | Hosted API | `https://api.deepseek.com` |
| Ollama, native app | Your computer | `http://127.0.0.1:11434` |
| Ollama, Docker API | Your computer | `http://host.docker.internal:11434` |
| OpenAI-compatible server, native app | Your computer or another host | The server's `/v1` base URL |
| OpenAI-compatible server, Docker API | A reachable host | A URL reachable from the container, often `http://host.docker.internal:8080/v1` |
| MLX, native API | Apple Silicon Mac | `http://127.0.0.1:8088/v1` |
| MLX, Docker API | Apple Silicon Mac host | `http://host.docker.internal:8088/v1` |

For Ollama, install and start it on the host, then download a model such as [`qwen3.5:9b`](https://ollama.com/library/qwen3.5):

```bash
ollama pull qwen3.5:9b
```

Ollama's model dropdown reads installed names from `/api/tags`. If the Docker API cannot reach the host server, configure Ollama to listen on a host interface reachable through `host.docker.internal`, and restrict that listener to trusted local networks. On Linux, Docker maps `host.docker.internal` through `host-gateway`; a process listening only on host loopback may not be reachable from the container. [Docker host networking reference](https://docs.docker.com/compose/how-tos/networking/)

For `llama.cpp`, start its OpenAI-compatible HTTP server with a model you already have, then choose **OpenAI-compatible API** in Settings. Use a loopback `/v1` URL when both Boundless and the model server run natively; use a container-reachable host URL for the Docker API. Boundless loads available model IDs from the server's `/models` endpoint. [llama.cpp server documentation](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)

MLX inference still runs on the Apple Silicon host when Boundless runs in Docker. Set `HOST_MLX_SUPPORTED=true` in your ignored `.env`, then restart the API with `docker compose up -d --build api`. Start `mlx_lm.server` on the Mac using the local model path below. In Model settings, choose **Apple Silicon · MLX**; Docker uses `http://host.docker.internal:8088/v1` automatically. The loaded model dropdown reads `/v1/models` from your MLX server. On other hosts, the MLX choice stays unavailable.

```bash
mlx_lm.server --model ~/.local/share/boundless/models/qwen3.5-9b-abliterated-mlx-4bit --host 127.0.0.1 --port 8088
```

If the container cannot reach a server bound to host loopback, bind the MLX server to a host interface reachable by Docker and keep that listener on a trusted local network. A successful `mlx_lm.generate` command confirms the model weights work, but the app also needs the HTTP server running.

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

Copy [.env.example](.env.example) for local configuration. Docker Compose uses `COMPOSE_LLM_PROVIDER`, `COMPOSE_LLM_BASE_URL`, and `COMPOSE_LLM_MODEL` so its Linux API can default to Ollama while native macOS development can default to MLX. Set `HOST_MLX_SUPPORTED=true` only on an Apple Silicon Mac; `MLX_HOST_BASE_URL` controls the Docker-reachable MLX endpoint. `APP_PORT`, `FRONTEND_PORT`, and `POSTGRES_PORT` control host ports. Changing `APP_PORT` requires rebuilding the web image because the browser API URL is compiled into the Next.js build.

Database records live in the Compose `postgres_data` volume. A DeepSeek key entered through Docker Model settings is saved with owner-only permissions in the `api_secrets` volume. A key entered through the native API is saved in the ignored `.secrets/deepseek_api_key` file. These are separate stores, so enter the key once in each environment you use. Keys are not included in campaign exports. A hosted provider receives the campaign context required for generation; a local provider keeps generation on the configured local endpoint.

The web and API ports are bound to host loopback. Boundless currently has no account system, so keep it on a trusted machine and avoid publishing those ports to the internet.

## How campaigns work

1. Boundless stores the original world prompt and derives a Campaign Constitution. Explicit hard rules are kept separately from summaries.
2. The context builder selects the current state, relevant lore and memories, summaries, and recent turns within a model budget.
3. The selected provider generates narration. CanonGuard checks for conflicts with hard rules and asks for a repair when needed.
4. A state interpretation pass writes validated changes to characters, items, relationships, events, and other campaign records, then creates a checkpoint.
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

The integration tests use local PostgreSQL and remove campaigns they create. `make test` and `make lint` run the backend tests and both linters from the repository root. To rebuild just the app images, run `docker compose build api web`.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `api` will not start | Run `docker compose ps` and `docker compose logs migrate api`. The migration step waits for a healthy database. |
| Port already in use | Set `APP_PORT`, `FRONTEND_PORT`, or `POSTGRES_PORT` in `.env`; rebuild the web image after changing `APP_PORT`. |
| Model offline | Check the endpoint in Model settings from the API's point of view. `127.0.0.1` inside a container refers to that container, not the host. |
| MLX unavailable in Docker | On an Apple Silicon Mac, set `HOST_MLX_SUPPORTED=true` in `.env` and recreate the API. On other hosts MLX remains unavailable. |
| MLX selected but offline | Start `mlx_lm.server` on the Mac, then use **Check again** in Model settings. The CLI generation command alone does not start the HTTP server. |
| DeepSeek key missing after switching to Docker | Re-enter the key in Docker Model settings; its named volume is separate from the native `.secrets` folder. |
| Campaigns seem missing | Confirm that the same Compose project and `postgres_data` volume are in use. `docker compose down` retains it; `down -v` removes it. |

## Contributing and license

The repository has a Next.js frontend, FastAPI backend, Alembic migrations, and a PostgreSQL 18 database with pgvector. Keep provider integrations in `backend/app/llm/`, campaign rules in `backend/app/services/`, and browser API calls in `frontend/src/lib/api.ts`. Run the relevant checks above and keep secrets, model weights, databases, and generated files out of Git. The root [.gitignore](.gitignore) and both `.dockerignore` files exclude local artifacts from commits and image build contexts.

A license has not yet been selected or added to this repository. Choose one before treating the source as an open-source release.
