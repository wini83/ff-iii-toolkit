.PHONY: dev-b dev-f backend-test backend-lint frontend-check frontend-lint frontend-build compose-up compose-down

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
