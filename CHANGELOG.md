# Changelog

All notable changes to Firefly III Toolkit are documented in this file.

The monorepo uses one product version for the backend and frontend. Component
changelogs under `backend/` and `frontend/` remain available as historical
records for releases before the migration.

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
