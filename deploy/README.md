# Production deployment

This directory deploys the two images emitted by one Firefly III Toolkit
release through a single Nginx gateway. The gateway routes `/api/` to the
backend and all other requests to the frontend.

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
its contents. This change only adds connectivity: all existing in-memory stores
remain unchanged. No existing application data is migrated into Redis.

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
