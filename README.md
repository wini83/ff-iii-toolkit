# Firefly III Toolkit

Monorepo containing the Firefly III Toolkit backend and web application.

## Layout

- `backend/` — FastAPI service, Alembic migrations, Python tooling, and tests.
- `frontend/` — SvelteKit application and its Node.js tooling.

Each component retains its own detailed documentation and changelog:
`backend/README.md`, `backend/CHANGELOG.md`, `frontend/DEVELOPING.md`, and
`frontend/CHANGELOG.md`.

## Potential duplicates (MVP)

Open **Transactions → Potential duplicates**, select one asset account and a date
range, then search. Candidates have exactly the same amount and currency, the
same direction on that account and different tag sets. Types may differ: a
transfer into the account can match a deposit, for example. The configurable
maximum date difference defaults to 2 days (0–7 supported); both transaction
dates must be inside the inclusive date range.

Partially overlapping tag sets and empty vs nonempty tags qualify. Equal sets,
including two empty sets, do not. Descriptions do not affect matching. Pairs are
ordered by their newer date descending, then transaction IDs ascending, with
pagination pinned to a snapshot; reload and search again if the snapshot changes.
**Reload transactions** forces a new Firefly fetch even while the cached snapshot
is still fresh. Opening the page uses the shared snapshot when available.
Repeated legitimate payments remain review candidates. Links open the original
transactions in Firefly. Clicking **Keep this** immediately adds the `do-usuniecia`
tag to the other transaction, preserving its existing tags. There is no preview
or confirmation step. **Keep both** leaves both transactions unchanged and can be
undone locally. Successfully kept copies remain protected across searches and
result pages in the same browser session. Shared transactions are marked once;
failed writes show an error and can be retried by clicking **Keep this** again.
Navigation and further actions are disabled while a write is in progress.
Filter by `do-usuniecia` in Firefly to review and delete the marked entries.
The toolkit does not delete transactions. The marker is ignored when comparing
tag sets, so tagging alone cannot produce new duplicate candidates. Search
results use the existing snapshot; use **Reload transactions** to fetch external
changes.

Filters are not saved and there are no persistent dismissals in this MVP.
Luciferin excludes multipart/split groups before building the shared snapshot;
the page reports their count and never compares individual split legs. Incoming
foreign-currency transfers are also excluded because the simplified snapshot
cannot reliably provide their destination-account amount. Reconciling opposite
sides of transfers across accounts is outside this feature's scope.

## Releases

The toolkit has one product version, stored in [`VERSION`](VERSION) and managed
centrally by Commitizen. The root [changelog](CHANGELOG.md) is generated from
Conventional Commits. Published releases provide both images with the same version:

- `ghcr.io/wini83/ff-iii-toolkit-backend:<version>`
- `ghcr.io/wini83/ff-iii-toolkit-frontend:<version>`

After a releasable Conventional Commit reaches `main`, the **Prepare release**
workflow opens a `release/v<version>` pull request. Review and merge that PR:
**Finalize release** creates an annotated tag at the release PR's merge commit
and a **draft GitHub Release**, with notes extracted only for that version.

Review the draft under **Releases**, then click **Publish release**. This starts
**Publish container images**, which checks out the release tag and publishes
both GHCR images with `<version>` and `latest` tags. Creating a tag or a draft
does not publish images.

To rebuild images for an already published release, open **Actions → Publish
container images → Run workflow**, select `main`, and enter `release_tag`
(for example `v3.1.0`). Missing releases and drafts are rejected; publish the
draft first. Recovery does not require another version bump or recreating a tag.

Use `make release-next` to preview the next version locally, and `make commit`
to create a Conventional Commit interactively. Do not create release tags
manually.

## Development

Python 3.14+ is required. The repository pins Python 3.14 in `.python-version`;
`uv sync` selects it automatically. Docker and CI use the same Python series.

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

### Redis with a host-run backend

The full local Compose stack configures Redis automatically. When running FastAPI
on your host (including VS Code debugging), start only the development Redis:

```sh
make redis-up
```

Set `REDIS_URL=redis://127.0.0.1:6379/0` in `backend/.env`, then start FastAPI as
usual. This separate development file publishes Redis only on loopback; do not
use it on the VPS. It uses the separate Compose project
`ff-iii-toolkit-redis-dev`, so starting or stopping it does not manage the full
stack's Redis. Do not override its project name with `-p` or `COMPOSE_PROJECT_NAME`
when using these commands. Redis contains disposable data and has no persistence.
Stop it with `make redis-down`.

Run the real Redis connection test against that instance:

```sh
cd backend
TEST_REDIS_URL=redis://127.0.0.1:6379/0 uv run pytest tests/test_redis_infrastructure.py
```

With `REDIS_URL` set, the backend stores transaction snapshots in Redis.
Freshness remains 300 seconds by default; Redis retention defaults to 24 hours.
See the deployment guide for schema validation, expiry and failure behavior.

The integration tests use unique keys with short TTLs and remove them afterward.
Without `TEST_REDIS_URL` it is skipped; Backend CI always supplies a real Redis
service. Redis setup, health behavior, VPS rollout, and rollback are documented in
[`deploy/README.md`](deploy/README.md#redis-infrastructure).
