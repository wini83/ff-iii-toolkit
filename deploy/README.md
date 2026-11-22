# Production deployment

This directory deploys the two images emitted by one Firefly III Toolkit
release through a single Nginx gateway. The gateway routes `/api/` to the
backend and all other requests to the frontend.

The API gateway accepts request bodies up to 3 MiB, allowing reconciliation's
2 MiB CSV limit plus multipart overhead. The backend still enforces its per-file
limit. If another reverse proxy sits in front of this gateway (including the
external Nginx in the VPS stack), allow at least `client_max_body_size 3m;` for
the API there as well. Reload that proxy after changing its configuration.

## Deploy a release

1. Create `deploy/.env` from `.env.example` and set
   `FF_III_TOOLKIT_VERSION` to a published release version, for example
   `3.0.0`.
2. Create `deploy/backend.env` from `backend.env.example` and replace its
   placeholder values with the production backend environment values. Do not
   commit this file.
3. Start the deployment:

   ```sh
   docker compose --env-file deploy/.env -f deploy/compose.production.yaml pull
   docker compose --env-file deploy/.env -f deploy/compose.production.yaml up -d
   ```

4. Verify the API and the browser-facing application:

   ```sh
   curl --fail http://localhost:8000/api/system/health
   curl --fail --output /dev/null http://localhost/
   ```

The `backend-data` named volume preserves the SQLite database across image
updates.

## Roll back

Set `FF_III_TOOLKIT_VERSION` in `deploy/.env` to the previously verified
release version, then run the same `pull` and `up -d` commands. The existing
`backend-data` volume is retained; do not use `docker compose down --volumes`
unless deliberately discarding application data.

## Redis infrastructure

The repository stacks start Redis on an internal `redis_private` network shared
only with the backend. No Redis port is published. Compose waits for Redis's
PING healthcheck and supplies `REDIS_URL` to the backend.
Redis has a 128 MiB container budget and a 64 MiB dataset limit, leaving room for
allocator/client overhead. `noeviction` rejects writes at the dataset limit;
measure real usage before selecting eviction rules for future stores.

Persistence is disabled (no AOF, snapshots, or volume). Recreating Redis loses
its contents. With `REDIS_URL` configured, transaction snapshots now use Redis.
Other stores (vault sessions, jobs, import previews) remain unchanged. A missing
snapshot is rebuilt from Firefly; the cache is never the source of truth.

Without `REDIS_URL`, startup is unchanged and health reports Redis as
`not_configured`. With it configured, a failed initial PING stops startup with a
generic message. Connection errors and URLs are not logged. Connection and command
timeouts default to 2 seconds each with no automatic retries. Each startup and
health PING also has a hard 2-second overall deadline, including connection and
handshake; URL query parameters cannot disable that probe deadline. Later failures make
`/api/system/health` return 503 and `external_services.redis=error`; successful
probes restore 200. Docker marks containers unhealthy but does not restart them
solely for that status. The client pool closes on shutdown.

### Existing VPS stack (ZeroTier + external Nginx)

For the stack using `ff-iii-toolkit-api`, `ff-iii-toolkit-ui`, and external
`firefly_net`, copy `compose.vps-redis.yaml` beside your `docker-compose.yml`.
This overlay preserves the proxy, certificates, data mount, UI settings, and
ZeroTier bindings. Only the API joins the new Redis network; Redis does not join
`firefly_net`.

1. Back up `docker-compose.yml` and `.env`. Set both toolkit image tags in the
   base file to the release containing this change (`3.3.4` does not include it).
2. Validate and apply:

   ```sh
   docker compose -f docker-compose.yml -f compose.vps-redis.yaml config --quiet
   docker compose -f docker-compose.yml -f compose.vps-redis.yaml pull
   docker compose -f docker-compose.yml -f compose.vps-redis.yaml up -d
   docker compose -f docker-compose.yml -f compose.vps-redis.yaml exec redis redis-cli ping
   docker compose -f docker-compose.yml -f compose.vps-redis.yaml exec ff-iii-toolkit-api curl --fail http://localhost:8000/api/system/health
   ```

   Expect `PONG` and `external_services.redis=ok`. Use both `-f` arguments for
   subsequent updates, otherwise Redis configuration will be omitted.
3. To roll back, restore both previous image tags and recreate from the base file:

   ```sh
   docker compose -f docker-compose.yml pull
   docker compose -f docker-compose.yml up -d --force-recreate ff-iii-toolkit-api ff-iii-toolkit-ui
   ```

   Once healthy, remove only Redis with the combined files:

   ```sh
   docker compose -f docker-compose.yml -f compose.vps-redis.yaml stop redis
   docker compose -f docker-compose.yml -f compose.vps-redis.yaml rm -f redis
   ```

   The base file has no `REDIS_URL`, so the recreated API no longer depends on
   Redis. Keep `./data`; do not use `down --volumes`.

For the repository production stack, version rollback follows the standard
procedure above. To disable Redis while retaining the new backend version,
remove its `REDIS_URL` and Redis `depends_on` entries, recreate the backend,
then stop Redis. This change requires no database migration or rollback.


### Transaction snapshot storage

`REDIS_URL` automatically selects `RedisSnapshotStore` in the API lifespan.
Without it, each API instance uses an isolated `InMemorySnapshotStore`. Unit tests
can inject an in-memory store into `create_app(snapshot_store=...)`. API services
using snapshots are scoped to that application lifespan, so an API restart does
not retain a closed Redis client or an old in-process snapshot service.

The single JSON key defaults to `ff-iii-toolkit:snapshot:v1:transactions` and is
configurable with `TRANSACTION_SNAPSHOT_REDIS_KEY`. Use a different key for each
independent Firefly dataset or dev/prod environment sharing a Redis instance.
Version 1 validates transactions, metrics, timezone-aware `fetched_at`, and
`schema_version`; amounts are JSON decimal strings, not floating-point values.
No pickle is used. Each replacement is one atomic `SET` including its expiry.

Freshness still uses `now - fetched_at > TRANSACTION_SNAPSHOT_TTL_SECONDS`
(default 300 seconds). `TRANSACTION_SNAPSHOT_RETENTION_SECONDS` (default 86400)
is the Redis expiry from the last successful write, independent of freshness.
Reads do not extend it. Stale snapshots remain available to the existing status
endpoint, with `is_stale=true` and the original `snapshot_fetched_at`/`expires_at`.
Snapshot consumers refresh stale data before using it; retaining a key never
extends the freshness window. Expiry or a Redis restart produces a cache miss.
An API process restart can reuse a still-fresh snapshot from the same Redis.

Malformed JSON or an unsupported schema version is treated as a cache miss and
logged with a generic message, without including transaction data. The next
successful Firefly fetch overwrites it. A read does not delete the malformed key,
which avoids deleting a newer value written by another process. Redis connection,
timeout, wrong-type, or memory-limit errors fail explicitly; snapshot endpoints
return HTTP 503 with a generic message. There is no automatic memory fallback and
no stale financial-data fallback after a failed refresh. Existing background
metrics jobs retain their FAILED/error response contract. Redis operations have a
2-second overall deadline. In-process refresh coalescing is preserved, with Redis coordination across
processes as described below.

For rollout, publish images containing this change and recreate the API with the
same private Redis setup. No migration or seed is needed; the first access fills
the cache. To revert to memory, remove `REDIS_URL` from the API environment
(including the Compose environment override) and recreate it. Cached Redis data
can expire naturally; keep the application database volume. Older images ignore
the cache and do not require converting it.

Backend CI runs shared memory/Redis store contracts and a restart test that writes
a snapshot through the API in one Python process, then starts a second API process
and verifies reuse without another Firefly fetch. To run them locally, start the
dev Redis and run `TEST_REDIS_URL=redis://127.0.0.1:6379/0 uv run pytest` from
`backend/`.

The VeloBank memory-exhaustion regression test additionally uses
`TEST_REDIS_OOM_URL`, pointing to a separate disposable Redis instance (CI uses
port 6380). This test temporarily changes server-wide `maxmemory` and eviction
policy settings and restores them afterward. Never point it at a production,
shared, or normal development Redis instance.


### Coordinated snapshot refresh (#109)

When Redis storage is selected, the application factory also injects a
`RedisRefreshCoordinator`. Memory-backed tests retain local coordination. The
snapshot JSON schema and HTTP response fields remain unchanged.

Two auxiliary keys are derived from `TRANSACTION_SNAPSHOT_REDIS_KEY`:

- `:refresh-lock`: a unique owner token acquired with `SET NX PX` and a finite
  lease (`TRANSACTION_SNAPSHOT_REFRESH_LEASE_SECONDS`, default 210 seconds).
- `:revision`: the token of the last successful publication. It expires with the
  snapshot, using the existing retention setting. Revisions do not use worker clocks.

The 210-second lease exceeds the existing 180-second Firefly fetch timeout. There
is no lease renewal. Owners re-read the snapshot after acquiring the lease. A
single Lua operation checks ownership, atomically writes the JSON/revision pair
with `MSET`, applies both expirations, and releases the lease. A worker whose lease
expired cannot publish or delete a successor's lock. Its discarded candidate is
never returned; it must observe a valid published winner or fail/retry. Redis
operations retain a 2-second limit and sanitized availability errors. Failed
fetches and cancellation release only the owner's token; if cleanup cannot reach
Redis, the lease expires naturally.

Fresh ordinary reads do not take the lock. Stale or missing reads normally share
one fetch. Force refresh pins the revision observed at entry: an existing fresh
snapshot alone does not satisfy it, but a fresh publication with a different
revision does. Concurrent force requests can share an in-flight refresh. A force
request observing a publication after an older local task has published waits
for that task and then requests another revision. Identical timestamps/data do
not collapse separate force requests.

Waiters poll the small revision marker with 200–400 ms jitter, reading the full
snapshot only at entry, after acquisition, or when a changed revision may satisfy
them. Snapshot/revision reads use atomic `MGET` as bytes so malformed UTF-8 remains
a cache miss. `TRANSACTION_SNAPSHOT_REFRESH_TIMEOUT_SECONDS` (default 220 seconds)
bounds each caller, including baseline reads and waiting behind an older local
task, and each coordination task. Cancelling one caller does not cancel shared
work; application shutdown explicitly cancels tasks and releases ownership before
closing Redis. Exhausting the budget produces an explicit availability failure
(HTTP 503 for synchronous snapshot consumers), never an old financial snapshot.
After a crashed owner consumes most of the budget, another attempt may be needed
to complete a long fetch. Reverse-proxy/client timeouts for synchronous refreshes
must allow the desired wait; existing background statistics can be polled normally.

A lease crash/expiry can cause another Firefly fetch; exactly one upstream request
cannot be guaranteed when a previous owner is paused or unreachable. The safety
guarantee is owner-checked publication and no overwrite by the expired owner.
This implementation targets the existing single Redis server, without distributed
Redis failover/Redlock or Redis Cluster support.

Deploy all API instances with this implementation before relying on coordination;
older versions can still perform unconditional writes. Existing snapshot JSON is
readable and a missing revision is supported. No database migration is required.
Rollback to the previous image leaves compatible snapshot JSON; auxiliary keys
expire naturally. Snapshot coordination alone does not migrate the other
process-local vault or temporary-file stores or make the entire API multi-worker ready.

Tests exercise independent service instances, concurrent normal/forced reads,
publication fencing after expiry, safe release, bounded waits and Firefly/Redis
failures. Real-process tests start two APIs against one Redis and kill a lock-owner
process to verify lease recovery. Run with the same `TEST_REDIS_URL` opt-in as the
snapshot store tests; Backend CI supplies it automatically.


### BLIK and Allegro matching caches (#111)

With `REDIS_URL`, both features share a Redis matching backend in the
`ff-iii-toolkit:{matching-v1}` namespace. Without it, application-scoped memory
stores implement the same contracts for development and tests. Successful HTTP
request/response schemas are unchanged.

Each matching result uses validated JSON schema version 1, preserving Decimal,
dates, transaction data, candidates and matching status. No vault cookie or
plaintext secret is stored. Entries have a fixed 600-second TTL, a 2 MiB payload
limit and at most 100 fields/pages per resource. Redis 8.2 (the Compose/CI version)
provides individual hash-field expiration: writing or reading another page does
not extend an older page's lifetime. Scope/revision metadata expires after 1800
seconds. A bounded number of global/feature generation markers intentionally
persists; invalidated financial payloads remain inaccessible and expire by their
original deadlines.

Cache scope includes the authenticated user and the CSV file or Allegro secret.
New BLIK uploads bind their temporary file identifiers to their owner, and cache
scope also includes the CSV content digest. **Re-upload BLIK files from older
versions**; legacy file IDs do not prove ownership. CSV files still live in the
API container's temporary directory; this migration does not add shared file
storage between containers.

Allegro cached operations recheck database ownership and an encrypted-credential
fingerprint without decrypting the credential. Rotation or deletion makes old
results inaccessible. Page keys include both limit and offset. Page/secret/all
invalidation is preserved; clearing all Allegro results leaves BLIK untouched.
Generation checks reject computations begun before an invalidation, even if they
finish afterward. Preview endpoints continue recomputing current matches.

Starting apply atomically reads the selected results and invalidates financial
matching caches across both features, because all users share the configured
Firefly ledger. Completion/cancellation and transaction category/tag updates
invalidate pending computations again. Applying a selection additionally reads
the current transaction from Firefly: changed date/amount/type/currency or a newly
processed transaction is rejected, and updates preserve current notes/tags.

Redis failures produce a sanitized 503 without falling back to another worker's
memory. Payload writes and apply admission fail before modifying data during
OOM. Invalidation alone may still run during OOM: it only deletes data or replaces
fixed-size generation tokens, allowing completed updates to fence stale results.

Apply jobs and metrics still execute in their local process. Their state is
shared through Redis as described below (#112). Job polling is portable across
workers; matching storage and job state do not make financial operations exactly-once.
BLIK temporary files and vault sessions still require compatible worker routing/storage;
keep the existing single-worker deployment until those concerns are addressed.

The normal `TEST_REDIS_URL` and isolated `TEST_REDIS_OOM_URL` test instances cover
cache hit/miss, fixed TTL, invalidation, concurrent access, fencing, owner isolation,
serialization, corruption and real memory exhaustion. All test records are synthetic.


### Asynchronous job state (#112)

`REDIS_URL` also selects `RedisJobStore`, shared by BLIK/Allegro apply and all
three metrics managers. JSON schema v1 records live under
`ff-iii-toolkit:{jobs-v1}`. Each record is limited to 2 MiB and contains the job
owner/type, execution token, status, progress/results and a lease deadline.
Nested data is stored as an opaque JSON string inside the envelope so Redis Lua
cannot change empty arrays or Decimal strings. HTTP response schemas are unchanged.
There is no Celery, queue consumer or automatic replay; `asyncio.Task` handles
remain local and are tracked until completion or application shutdown.

Apply admission reserves a durable job **before** consuming matching results.
A ledger-wide atomic claim allows only one active apply across both features.
If consuming the preview fails, the reservation is marked failed and no financial
request starts. If the process disappears between those steps, its pending job
expires through the same interruption protocol. Repeating an already-consumed
preview cannot start another apply. A concurrent active apply returns HTTP 409;
Redis failures return sanitized HTTP 503 without a memory fallback.

Executions renew a 120-second lease every 40 seconds. Redis server time controls
lease checks. Every progress write validates the execution token, active status
and deadline. Expired pending/running records become `failed` on their next
poll/claim, retaining confirmed results and indicating any transaction that was
in flight. Late writers cannot publish over a terminal job. Heartbeat/storage
failure cancels local work, and progress persistence is required before each
financial request and after each outcome. Subsequent financial requests stop
when persistence fails. Graceful shutdown cancels and awaits owned tasks before
closing Redis; hard process death is detected by lease expiry.

A request already accepted by Firefly cannot be rolled back or fenced by Redis.
If the process or connection fails after Firefly commits but before the result is
saved, that transaction has an uncertain outcome. Restart never repeats it: read
the failure, verify the result and generate a new matching preview. This provides
single active admission while the lease is valid and **no automatic retries of
financial writes**, not exactly-once execution under partitions or process pauses.
Redis state itself has the durability of the configured Redis persistence; losing
Redis data loses job history and leases. The existing dedicated `noeviction`
configuration is required; do not use these records as an accounting audit log.

Completed/failed records expire 24 hours after finalization. Polling does not
extend that retention; active heartbeats refresh it. Metrics use shared claims
per feature and keep snapshot-timestamp freshness checks. Failed/interrupted
metrics remain visible until an explicit refresh (or record expiry), which can
safely recompute them. Ownership for apply polling is persisted with the job,
so results remain readable even if its temporary file or Allegro secret is removed;
other users cannot read them. Memory mode follows the same lifecycle but cannot
survive a process restart.

Tests cover independent Redis clients, competing claims, TTL, heartbeats, stale
execution rejection, application/process restart, owner checks, per-transaction
checkpoint failures, immediate shutdown and real Redis OOM. The existing
`TEST_REDIS_URL`/isolated `TEST_REDIS_OOM_URL` opt-ins apply.
