# Changelog

All notable changes to Firefly III Toolkit are documented in this file.

The monorepo uses one product version for the backend and frontend. Component
changelogs under `backend/` and `frontend/` remain available as historical
records for releases before the migration.

## v3.8.2 (2026-11-23)

### Fix

- **reconciliation**: accept Alior CSV counterparty quotes (#166)

## v3.8.1 (2026-11-22)

### Fix

- **release**: authenticate tag pushes and support finalization recovery (#160)
- **reconciliation**: include all transaction types and allow statement uploads (#159)

## v3.8.0 (2026-11-22)

### Feat

- **reconciliation**: compare bank statements and mark verified matches (#157)

## v3.7.0 (2026-11-22)

### Feat

- **duplicates**: immediately tag the other copy when keeping a transaction (#155)

## v3.6.0 (2026-11-19)

### Feat

- **tx**: add account-scoped duplicate candidate search (#151)
- **metrics**: add Allegro and BLIK amount statistics (#150)
- **ui**: replace workspace landing page with compact task overview (#147)
- **ui**: streamline transaction screening work view (#146)
- **ui**: compact application shell and navigation (#145)
- **ui**: introduce compact wireframe and black themes (#143)

### Fix

- **ui**: make statistics readable across currencies and themes (#148)

### Refactor

- **ui**: apply compact styling to remaining workflows (#149)

## v3.5.0 (2026-11-17)

### Feat

- **statistics**: inspect unprocessed Allegro and BLIK transactions (#134)

### Refactor

- **blik**: remove legacy incomplete processing metrics (#133)

## v3.4.2 (2026-11-17)

### Refactor

- **jobs**: persist asynchronous apply and metrics state in Redis (#131)

## v3.4.1 (2026-11-16)

### Fix

- **snapshot**: coordinate refresh across backend workers (#120)

## v3.4.0 (2026-11-15)

### Feat

- **snapshot**: persist transaction snapshots in Redis (#114)

## v3.3.4 (2026-10-05)

### Fix

- **velobank**: recognize legacy card merchants and clean descriptions (#62)

## v3.3.3 (2026-10-04)

### Fix

- **frontend**: migrate configuration for SvelteKit 3 (#50)

## v3.3.2 (2026-10-04)

### Fix

- support multi-currency transaction statistics (#46)

## v3.3.1 (2026-10-04)

### Refactor

- add amount view to transaction statistics (#43)

## v3.3.0 (2026-10-04)

### Feat

- add VeloBank PDF web import with preview and CSV export (#41)

## v3.2.0 (2026-10-02)

### Feat

- **import**: add local VeloBank PDF to CSV CLI (#39)

## v3.1.0 (2026-10-01)

### Feat

- consume Firefly connection status through Luciferin (#34)

## v3.0.1 (2026-09-13)

### Fix

- **release**: the system version endpoint reports the unified monorepo release
  version from the published backend image (#14).

## v3.0.0 (2026-09-13)

### Feat

- Unified backend and frontend source code in one repository.
- Root CI, dependency management, Docker Compose, and release tooling.
- Versioned backend and frontend container images published from one release.

### Fix

- **frontend**: track shared library sources.

### Changed

- The project is released as one product starting at version 3.0.0.
