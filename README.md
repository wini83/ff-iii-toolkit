# Firefly III Toolkit

Monorepo containing the Firefly III Toolkit backend and web application.

## Layout

- `backend/` — FastAPI service, Alembic migrations, Python tooling, and tests.
- `frontend/` — SvelteKit application and its Node.js tooling.

Each component retains its own detailed documentation and changelog:
`backend/README.md`, `backend/CHANGELOG.md`, `frontend/DEVELOPING.md`, and
`frontend/CHANGELOG.md`.

## Development

Configure the backend first:

```sh
cp .env.example backend/.env
cd backend
uv sync --frozen --dev
uv run alembic upgrade head
PYTHONPATH=src uv run uvicorn main:create_production_app --factory --reload --app-dir src
```

In a second terminal, start the frontend:

```sh
cd frontend
npm ci
npm run dev
```

After the dependencies are installed, the same development servers can be
started from the repository root in separate terminals:

```sh
make dev-b
make dev-f
```

Refresh local installations from the committed lockfiles with
`make deps-refresh`. To update dependency versions and their lockfiles, use
`make deps-update` and commit the resulting `backend/uv.lock` and
`frontend/package-lock.json` changes.

Run component checks from their respective directories:

```sh
cd backend && uv run pytest
cd frontend && npm run check && npm run lint && npm run build
```

## Containers

Build and start the local stack from the repository root after creating
`backend/.env`:

```sh
docker compose up --build
```

The backend is available on port 8000 and the frontend on port 3000. The same
commands are available through the root `Makefile`, for example
`make backend-test` and `make frontend-build`.
