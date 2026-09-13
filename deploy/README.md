# Production deployment

This directory deploys the two images emitted by one Firefly III Toolkit
release through a single Nginx gateway. The gateway routes `/api/` to the
backend and all other requests to the frontend.

## Deploy a release

1. Create `deploy/.env` from `.env.example` and set
   `FF_III_TOOLKIT_VERSION` to a published release version, for example
   `2.0.0`.
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
