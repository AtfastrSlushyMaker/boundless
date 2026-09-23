.PHONY: stack-up stack-down stack-logs db-up db-down migrate api web test lint mlx mlx-host

stack-up:
	@if [ "$$(uname -s)" = Darwin ] && [ "$$(uname -m)" = arm64 ]; then \
		if command -v python3 >/dev/null 2>&1; then python3 scripts/mlx_host.py --ensure || echo "MLX launcher unavailable; run make mlx-host after setup."; fi; \
		HOST_MLX_SUPPORTED=true docker compose up -d --build --wait; \
	else \
		docker compose up -d --build --wait; \
	fi

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

mlx-host:
	python3 scripts/mlx_host.py --ensure
