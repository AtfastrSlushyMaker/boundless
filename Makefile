.PHONY: stack-up stack-down stack-logs db-up db-down migrate api web test lint mlx

stack-up:
	docker compose up -d --build --wait

stack-down:
	docker compose down

stack-logs:
	docker compose logs -f api web

db-up:
	docker compose up -d --wait db

db-down:
	docker compose stop db

migrate:
	cd backend && uv run alembic upgrade head

api:
	cd backend && uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

web:
	cd frontend && npm run dev

test:
	cd backend && uv run pytest

lint:
	cd backend && uv run ruff check .
	cd frontend && npm run lint

mlx:
	mlx_lm.server --model lukey03/Qwen3.5-9B-abliterated-MLX-4bit --host 127.0.0.1 --port 8088
