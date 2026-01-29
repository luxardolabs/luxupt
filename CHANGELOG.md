# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2025-10-15

### Added
- **Poetry Integration**: Migrated from requirements.txt to Poetry for modern dependency management
  - Created `pyproject.toml` with full project metadata and configuration
  - Added development dependencies (pytest, black, ruff, mypy, etc.)
  - Configured code quality tools (black, isort, mypy, ruff, pytest)
- **Comprehensive Makefile**: Replaced build.sh with a full-featured Makefile
  - Auto-detects version from directory structure (/major.minor/patch)
  - Supports multi-architecture builds (linux/amd64, linux/arm64)
  - Separate targets for local registry and Docker Hub
  - Includes Docker cache optimization (pull previous image, BuildKit inline cache)
  - Poetry integration commands (install, update, lock)
  - Code quality commands (format, lint, test)
  - Flexible build/push operations for different registries
- **Multi-stage Dockerfile**: Optimized Docker build with Poetry
  - Two-stage build for smaller final image
  - Non-root user (appuser) for security
  - Health check included
  - Proper layer caching for faster rebuilds
- **Docker Compose Overlays**: Refactored deployment configuration
  - Base `compose.yaml` with common settings
  - Site-specific overlays (`compose.site-overlay.yaml`, `compose.bb-overlay.yaml`)
  - DRY principle - no configuration duplication
  - Consistent hyphenated naming convention
- **GitHub Actions CI/CD**: Prepared for automated workflows
- **.dockerignore**: Comprehensive exclusion list for Docker builds

### Changed
- **Project Naming Convention**: 
  - Repository: `luxardolabs/unifi-protect-time-lapse` (hyphenated)
  - Container names: `unifi-protect-time-lapse-*` (hyphenated)
  - Service names: `unifi-protect-time-lapse` (hyphenated)
- **Version Scheme**: Migrated from date-based (2025.6.4) to semantic versioning (1.1.0)
- **Python Version**: Upgraded from 3.11 to 3.13 in all configurations
- **Registry Structure**: Now uses `registry.internal:5000/luxardolabs/unifi-protect-time-lapse`
- **Dependencies Updated** to latest versions:
  - httpx: 0.26.0 → 0.28.1
  - urllib3: 1.26.0 → 2.5.0
  - fastapi: 0.104.1 → 0.119.0
  - uvicorn: 0.24.0 → 0.37.0
  - python-multipart: 0.0.6 → 0.0.20
  - aiofiles: 23.2.1 → 25.1.0
  - pillow: 10.1.0 → 11.3.0
  - psutil: 5.9.6 → 7.1.0
  - All dev dependencies updated to latest versions

### Removed
- `build.sh` - Functionality moved to Makefile
- `requirements.txt` - Using Poetry exclusively
- `gather_reqs.sh` - No longer needed with Poetry
- Individual compose files - Replaced with base + overlays pattern
- `poetry-export` target from Makefile - Not needed without requirements.txt

### Technical Details
- **Multi-Architecture Support**: Both amd64 and arm64 builds supported
- **Build Optimizations**: 
  - Docker BuildKit enabled
  - Cache-from previous builds
  - BuildKit inline cache
  - Proper layer ordering for optimal caching
- **Security Improvements**:
  - Non-root container user
  - Health checks included
  - Proper Python environment isolation

### Migration Notes
- To build: Use `make docker-push-local` instead of `./build.sh`
- To deploy: Use `docker compose -f compose.yaml -f compose.<site>-overlay.yaml up -d`
- Poetry must be installed for local development
- Python 3.13 is now required