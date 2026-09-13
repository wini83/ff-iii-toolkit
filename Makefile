.DEFAULT_GOAL := help

.PHONY: help deps-refresh deps-update dev-b dev-f backend-test backend-lint frontend-check frontend-lint frontend-build compose-up compose-down

help:
	@printf '%s\n' \
		'Firefly III Toolkit commands:' \
		'  make deps-refresh   Install dependencies from committed lockfiles.' \
		'  make deps-update    Update dependency versions and lockfiles.' \
		'  make dev-b          Start the backend development server.' \
		'  make dev-f          Start the frontend development server.' \
		'  make backend-test   Run backend tests.' \
		'  make backend-lint   Run the backend linter.' \
		'  make frontend-check Run the frontend typecheck.' \
		'  make frontend-lint  Run the frontend linter.' \
		'  make frontend-build Build the frontend.' \
		'  make compose-up     Build and start the Compose stack.' \
		'  make compose-down   Stop the Compose stack.'

deps-refresh:
	cd backend && uv sync --frozen --dev
	cd frontend && npm ci

deps-update:
	cd backend && uv lock --upgrade && uv sync --dev
	cd frontend && npm update

dev-b:
	$(MAKE) -C backend dev

dev-f:
	$(MAKE) -C frontend dev

backend-test:
	cd backend && uv run pytest

backend-lint:
	cd backend && uv run ruff check .

frontend-check:
	cd frontend && npm run check

frontend-lint:
	cd frontend && npm run lint

frontend-build:
	cd frontend && npm run build

compose-up:
	docker compose up --build

compose-down:
	docker compose down
