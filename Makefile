.PHONY: install infra-up infra-down db-upgrade api worker web format lint type-check test build check

install:
	uv sync --all-packages
	pnpm install
	pnpm rebuild esbuild

infra-up:
	docker compose -f deploy/docker-compose.yml up -d

infra-down:
	docker compose -f deploy/docker-compose.yml stop

db-upgrade:
	uv run --package supportops-api alembic upgrade head

api:
	uv run --package supportops-api supportops-api

worker:
	uv run --package supportops-agent-worker supportops-worker

web:
	pnpm run web:dev

format:
	uv run ruff format .

lint:
	uv run ruff check .

type-check:
	uv run mypy
	pnpm run web:type-check

test:
	uv run pytest

build:
	pnpm run web:build

check: lint type-check test build
	openspec validate establish-enterprise-support-agent-foundation --strict
