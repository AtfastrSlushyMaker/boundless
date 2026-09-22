# Boundless

Boundless is a local-first, open-source text role-playing game. You describe a world in natural language, a local language model acts as Game Master, and a state interpreter records what actually happened. The campaign constitution preserves hard rules such as immortality, while PostgreSQL holds the story, characters, relationships, locations, inventory, memories, secrets, and checkpoints.

## Requirements

- Docker Desktop or another Docker Compose runtime
- Node.js 20.9+ and npm
- Python 3.11+ with [uv](https://docs.astral.sh/uv/)
- For the recommended model: Apple Silicon with about 6 GB of free disk space and [MLX LM](https://github.com/ml-explore/mlx-lm)

## Start locally

From the repository root:

```bash
cp .env.example .env
docker compose up -d --wait
cd backend
uv sync --extra dev
uv run alembic upgrade head
```

Start local inference in a separate terminal. On Apple Silicon:

```bash
cd backend
uv sync --extra mlx
uv run mlx_lm.server --model lukey03/Qwen3.5-9B-abliterated-MLX-4bit --host 127.0.0.1 --port 8088
```

The first MLX launch downloads the model. The recommended model is roughly 5 GB, and generation starts only after its weights are loaded. You can use Ollama or another OpenAI-compatible server instead. Set `LLM_PROVIDER`, `LLM_BASE_URL`, and `LLM_MODEL` in `.env`, or choose the runtime in the UI. No cloud model is selected automatically.

Run the API and web app in two more terminals:

```bash
cd backend
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

```bash
cd frontend
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000). The API health endpoint is [http://127.0.0.1:8000/api/health](http://127.0.0.1:8000/api/health). Docker PostgreSQL listens only on `127.0.0.1:54329`, with data in a named volume. The model runs natively, outside Docker.

## How campaigns work

1. Your opening prompt is saved unchanged, then parsed into a constitution. Explicit hard rules are added to the `canon_rules` table.
2. The Game Master receives the constitution, current state, relevant entities and memories, and recent turns within a bounded context budget.
3. Narration streams to the reader. The CanonGuard checks for contradictions and requests a repair before saving a violating response.
4. A separate state interpretation pass emits validated operations. The server applies those operations to normalized records and writes a checkpoint.
5. Editing or rewriting a response restores the previous checkpoint and interprets the revised narration. Rewind restores a selected checkpoint. Branching clones branch-scoped state so alternate timelines can diverge.

Hidden facts are stored with `GM_ONLY` visibility and are excluded from the player's lore panels. They are generated only when the initial premise permits a hidden exception. The model can still make mistakes; hard canon is defended by both prompt instructions and server-side validation.

## Data and controls

The archive can rename, archive, restore, duplicate, export, import, and delete campaigns. Exports use versioned `.boundless.json` files and include branches, turns, versions, checkpoints, entities, and model profile metadata. API keys are read from `.env` and excluded from exports. Model settings can be edited in the UI. `/canon`, `/retcon`, and `/ooc` are meta commands in the action field.

## Development

```bash
make db-up
make migrate
make api
make web
make test
make lint
```

Or run `cd backend && uv run pytest`, `cd backend && uv run ruff check .`, and `cd frontend && npm run lint && npm run build` separately. The integration test uses the local PostgreSQL database and deletes campaigns it creates.

## Troubleshooting

- Database unavailable: start Docker Desktop, then run `docker compose up -d --wait` and `cd backend && uv run alembic upgrade head`.
- Model offline: verify your local model server is running and matches the endpoint and model identifier shown in Settings. The default MLX endpoint is `http://127.0.0.1:8088/v1`.
- First scene takes time: MLX loads the model on its first generation. The initial download can take longer depending on your connection.
- Import rejected: use a Boundless version 1 export smaller than 20 MB. Invalid or oversized collections are rejected.

## Repository layout

- `frontend/`: Next.js reader and campaign archive
- `backend/app/api/`: HTTP and streaming routes
- `backend/app/services/`: constitution, state, canon, memory, timeline, and export logic
- `backend/app/db/`: normalized SQLAlchemy models
- `backend/alembic/`: PostgreSQL migrations
- `docker-compose.yml`: PostgreSQL 18 with pgvector

## License

License selection is pending the project owner's preference.

## Optional DeepSeek Flash trial

Boundless can use the hosted [DeepSeek API](https://api-docs.deepseek.com/quick_start/pricing/) when you explicitly choose **DeepSeek · hosted** in Model settings. **DeepSeek V4.1 Flash** is the default model, using the API identifier `deepseek-flash`. This sends the campaign constitution and relevant story context to DeepSeek for generation. Campaign records remain in your local PostgreSQL database. Enter your API key in the masked settings field, load the [model list](https://api-docs.deepseek.com/api/list-models/) from DeepSeek's `/models` endpoint, choose a model, and save. The key is stored in the ignored local `.secrets/deepseek_api_key` file with owner-only permissions. It is never returned by the settings API, stored in the database, or included in campaign exports. You can alternatively set `DEEPSEEK_API_KEY` in `.env` and restart the API. Boundless uses non-thinking mode for this profile to keep interactive turns responsive and costs predictable.

When Ollama is selected, the model selector loads installed model names from your configured Ollama server's `/api/tags` endpoint through the Boundless API. Choose one of those names before saving.
