# Changelog

All notable changes to Firefly III Toolkit are documented in this file.

The monorepo uses one product version for the backend and frontend. Component
changelogs under `backend/` and `frontend/` remain available as historical
records for releases before the migration.

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
