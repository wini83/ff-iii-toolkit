# Firefly III Toolkit

Monorepo containing the Firefly III Toolkit backend and web application.

## Layout

- `backend/` — FastAPI service, Alembic migrations, Python tooling, and tests.
- `frontend/` — SvelteKit application and its Node.js tooling.

Each component retains its own detailed documentation and changelog:
`backend/README.md`, `backend/CHANGELOG.md`, `frontend/DEVELOPING.md`, and
`frontend/CHANGELOG.md`.

## Releases

The toolkit has one product version, stored in [`VERSION`](VERSION) and managed
centrally by Commitizen. The root [changelog](CHANGELOG.md) is generated from
Conventional Commits. A pushed tag matching `v<version>` creates a GitHub
release and publishes both images with the same version:

- `ghcr.io/wini83/ff-iii-toolkit-backend:<version>`
- `ghcr.io/wini83/ff-iii-toolkit-frontend:<version>`

After a releasable Conventional Commit reaches `main`, the **Prepare release**
workflow opens a `release/v<version>` pull request. Review and merge that PR to
finalize the release: the **Finalize release** workflow creates the annotated
tag at the release PR's merge commit and directly calls the reusable **Release**
workflow to publish the GitHub release and both images. This does not depend on
a tag push made with `GITHUB_TOKEN` triggering another workflow.

To recover publication for an existing tag, open **Actions → Release → Run
workflow**, select `main`, and enter the tag (for example `v3.1.0`). The workflow
checks out that tag and verifies its `VERSION` before publishing. Recovery does
not require another version bump or recreating the tag. A user-pushed release
tag can also trigger the same publication workflow.

Use `make release-next` to preview the next version locally, and `make commit`
to create a Conventional Commit interactively. Do not create release tags
manually.

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

Run `make help` to list all root-level commands.

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

The backend is available on port 8000, the frontend on port 3000, and the
integrated gateway on port 8080. The same commands are available through the root `Makefile`, for example
`make backend-test` and `make frontend-build`.

For a versioned production deployment and rollback procedure, see
[`deploy/README.md`](deploy/README.md).
