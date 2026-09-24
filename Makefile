.PHONY: stack-up stack-down stack-logs db-up db-down migrate api web test test-db lint mlx mlx-host repair

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

TEST_DATABASE_URL ?= postgresql+asyncpg://boundless:boundless_dev@127.0.0.1:54329/boundless_test

test-db:
	docker compose exec -T db psql -U boundless -d boundless -tc "SELECT 1 FROM pg_database WHERE datname='boundless_test'" | grep -q 1 || docker compose exec -T db psql -U boundless -d boundless -c "CREATE DATABASE boundless_test"
	cd backend && DATABASE_URL=$(TEST_DATABASE_URL) uv run alembic upgrade head

test: test-db
	cd backend && TEST_DATABASE_URL=$(TEST_DATABASE_URL) uv run pytest

repair:
	cd backend && uv run python -m app.cli repair-campaign $(CAMPAIGN)

lint:
	cd backend && uv run ruff check .
	cd frontend && npm run lint

mlx:
	mlx_lm.server --model lukey03/Qwen3.5-9B-abliterated-MLX-4bit --host 127.0.0.1 --port 8088

mlx-host:
	python3 scripts/mlx_host.py --ensure
