# Makefile for luxupt
# Automatically detects version from directory structure

# Color definitions
GREEN := \033[0;32m
YELLOW := \033[0;33m
BLUE := \033[0;34m
RED := \033[0;31m
NC := \033[0m # No Color

# Project name from pyproject.toml
PROJECT_NAME := luxupt

# Version — source of truth is the VERSION file at repo root. Bump it on release.
# (Previously auto-detected from a /major.minor/patch directory path; that silently
#  broke once the app moved to /mnt/luxardolabs/luxupt and fell back to a stale
#  pyproject.toml. Now aligned with the fleet standard — luxmark/luxswirl.)
CURRENT_DIR := $(shell pwd)
VERSION := $(shell cat VERSION 2>/dev/null || git -c safe.directory=$(CURRENT_DIR) describe --tags --always 2>/dev/null || echo "0.0.0-dev")

# Allow manual override
ifdef MANUAL_VERSION
    VERSION := $(MANUAL_VERSION)
endif

ifeq ($(strip $(VERSION)),)
    $(error Unable to determine version — add a VERSION file at repo root or set MANUAL_VERSION=x.y.z)
endif

# Private registry hosts live OUT of git (this Makefile is committed — public repo).
# Real hosts in a gitignored Makefile.local (copy Makefile.local.example); empty here so a
# clean clone is buildable and guard-version-check skips cleanly when unset.
# (FLEET-BUILD-DEPLOY-STANDARD — repo.build_config_committed.)
-include Makefile.local

# Docker registry settings
LOCAL_REGISTRY ?=
DOCKER_HUB_USER := luxardolabs
GITHUB_USER := luxardolabs
LOCAL_IMAGE := $(LOCAL_REGISTRY)/$(DOCKER_HUB_USER)/$(PROJECT_NAME)
DOCKER_HUB_IMAGE := $(DOCKER_HUB_USER)/$(PROJECT_NAME)
GHCR_IMAGE := ghcr.io/$(GITHUB_USER)/$(PROJECT_NAME)

# Load .env file if present (for GHCR_TOKEN and other credentials)
ifneq (,$(wildcard .env))
    include .env
    export GHCR_TOKEN
endif

# Registry credentials (loaded from .env or environment)
GHCR_TOKEN ?= $(error GHCR_TOKEN not set — add to .env or export it)

# Build settings
BUILD_DATE := $(shell date -u +"%Y-%m-%dT%H:%M:%SZ")
# Git revision for OCI image provenance (repo.oci_image_labels)
REVISION := $(shell git -c safe.directory=$(CURRENT_DIR) rev-parse HEAD 2>/dev/null || echo unknown)
# For quick local development builds - amd64 only (faster iteration)
PLATFORM_DEV := linux/amd64
# For release builds - multi-arch (amd64 + arm64)
PLATFORM := linux/amd64,linux/arm64
DOCKER_BUILDKIT := 1

# Python settings
PYTHON := python3.13
POETRY := poetry

# Default target
.DEFAULT_GOAL := help

.PHONY: help
help: ## Show this help message
	@echo '$(GREEN)luxupt Makefile$(NC)'
	@echo ''
	@echo '$(BLUE)Detected Configuration:$(NC)'
	@echo '  Directory: $(CURRENT_DIR)'
	@echo '  Version: $(VERSION)'
	@echo '  Local Registry: $(LOCAL_IMAGE):$(VERSION)'
	@echo '  Docker Hub: $(DOCKER_HUB_IMAGE):$(VERSION)'
	@echo ''
	@echo '$(BLUE)Available targets:$(NC)'
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  $(GREEN)%-20s$(NC) %s\n", $$1, $$2}' $(MAKEFILE_LIST)

.PHONY: validate-version
validate-version: ## Validate version detection
	@echo '$(BLUE)Version Validation:$(NC)'
	@echo '  Source: VERSION file (fallback: git describe)'
	@echo '  Version: $(VERSION)'
	@if [ -z "$(VERSION)" ]; then \
		echo '$(RED)ERROR: Could not determine version$(NC)'; \
		echo 'Add a VERSION file at repo root or set MANUAL_VERSION=x.y.z'; \
		exit 1; \
	else \
		echo '$(GREEN)Version validation passed!$(NC)'; \
	fi

.PHONY: validate-structure
validate-structure: ## Validate project structure
	@echo '$(BLUE)Validating project structure...$(NC)'
	@if [ ! -d "app" ]; then \
		echo '$(RED)ERROR: app directory not found$(NC)'; \
		exit 1; \
	fi
	@if [ ! -f "pyproject.toml" ]; then \
		echo '$(RED)ERROR: pyproject.toml not found$(NC)'; \
		exit 1; \
	fi
	@if [ ! -f "app/config.py" ] || [ ! -f "app/camera_manager.py" ] || [ ! -f "app/main.py" ]; then \
		echo '$(RED)ERROR: Required source files missing in app directory$(NC)'; \
		exit 1; \
	fi
	@echo '$(GREEN)Project structure validation passed!$(NC)'

.PHONY: install
install: ## Install Poetry dependencies
	@echo '$(BLUE)Installing dependencies with Poetry...$(NC)'
	$(POETRY) install

.PHONY: install-prod
install-prod: ## Install only production dependencies
	@echo '$(BLUE)Installing production dependencies...$(NC)'
	$(POETRY) install --only main

.PHONY: update
update: ## Update dependencies
	@echo '$(BLUE)Updating dependencies...$(NC)'
	$(POETRY) update

.PHONY: lock
lock: ## Update poetry.lock file
	@echo '$(BLUE)Updating poetry.lock file...$(NC)'
	$(POETRY) lock --no-update

# --- Poetry in Docker (no local poetry needed; matches the Dockerfile's version) ---
POETRY_IN_DOCKER_VERSION := 1.7.1
define poetry_docker
	docker run --rm -v $(PWD):/app -w /app python:3.13-slim \
	  sh -c "pip install -q poetry==$(POETRY_IN_DOCKER_VERSION) && poetry $(1)"
endef

.PHONY: poetry-lock
poetry-lock: ## Regenerate poetry.lock in a container (no local poetry needed)
	@echo '$(BLUE)poetry lock (in docker)...$(NC)'
	$(call poetry_docker,lock --no-update)

.PHONY: poetry-update
poetry-update: ## Update deps to latest allowed + rewrite the lock (in a container)
	@echo '$(BLUE)poetry update --lock (in docker)...$(NC)'
	$(call poetry_docker,update --lock)

.PHONY: poetry-install
poetry-install: ## Verify deps resolve + install from lock in a throwaway container
	@echo '$(BLUE)poetry install (in docker)...$(NC)'
	$(call poetry_docker,install --no-root --only main)

# --- Secret scanning (gitleaks in Docker; CANONICAL fleet denylist, no local config) ---
# The gitleaks config is the fleet's, EMITTED at scan time (luxlint --emit-config gitleaks) to
# /tmp and mounted — NEVER committed (its denylist names the strings we keep out of repos;
# secret.no_local_gitleaks_config flags a local .gitleaks.toml, same as a local ruff.toml).
GITLEAKS_IMG := ghcr.io/gitleaks/gitleaks:latest
GITLEAKS_CFG := /tmp/luxlint.gitleaks.toml
# Trust the mounted repo regardless of container uid vs NFS file owner (avoids git's
# "dubious ownership" check) via git's GIT_CONFIG_* env overrides.
GITLEAKS_RUN := docker run --rm \
	-e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory -e GIT_CONFIG_VALUE_0=/repo \
	-v $(PWD):/repo -w /repo -v $(GITLEAKS_CFG):/cfg.toml:ro $(GITLEAKS_IMG)

.PHONY: gitleaks
gitleaks: ## Scan committed history for secrets (run before pushing)
	@echo '$(BLUE)Scanning committed history for secrets...$(NC)'
	@docker run --rm -v $(PWD):/repo $(LUXLINT_IMAGE) --emit-config gitleaks > $(GITLEAKS_CFG)
	$(GITLEAKS_RUN) git /repo -c /cfg.toml --redact --no-banner -v

.PHONY: gitleaks-staged
gitleaks-staged: ## Scan STAGED changes for secrets (good as a pre-commit check)
	@echo '$(BLUE)Scanning staged changes for secrets...$(NC)'
	@docker run --rm -v $(PWD):/repo $(LUXLINT_IMAGE) --emit-config gitleaks > $(GITLEAKS_CFG)
	$(GITLEAKS_RUN) git /repo -c /cfg.toml --staged --redact --no-banner -v

# Code-style + type guard (luxlint) — pinned; host from Makefile.local ($(LUXARCH_REGISTRY)).
LUXLINT_VERSION ?= 0.16.1
LUXLINT_IMAGE   ?= $(LUXARCH_REGISTRY)/luxardolabs/luxlint:$(LUXLINT_VERSION)
# Lean base for the mypy tail (tools installed FRESH each run, never inherited from :dev —
# FLEET-BUILD-DEPLOY-STANDARD "Lint & test images"). Pytest deps come from the lock via Dockerfile.test.
LUXLINT_MYPY_IMAGE ?= python:3.13-slim
TEST_DEPS_IMAGE    ?= luxupt-test-deps

.PHONY: format
format: ## Format Python with luxlint's canonical ruff (format + autofix), byte-identical to the fleet
	@echo '$(BLUE)Formatting (luxlint ruff)...$(NC)'
	@docker run --rm -v $(PWD):/repo $(LUXLINT_IMAGE) --emit-config ruff > /tmp/luxlint.ruff.toml
	docker run --rm -v $(PWD):/repo -v /tmp/luxlint.ruff.toml:/cfg/ruff.toml:ro --entrypoint ruff $(LUXLINT_IMAGE) format --config /cfg/ruff.toml app
	@# --fix applies the safe autofixes; remaining un-autofixable reds are the burn-down (not a format failure).
	-docker run --rm -v $(PWD):/repo -v /tmp/luxlint.ruff.toml:/cfg/ruff.toml:ro --entrypoint ruff $(LUXLINT_IMAGE) check --config /cfg/ruff.toml --fix app

.PHONY: lint
lint: guard-version-check ## luxlint (ruff, mount-only) + the mypy tail + JS eslint — ONE recipe; fails if any fails
	@# HONESTY: the tail installs pydantic so luxlint's auto-injected `plugins = pydantic.mypy`
	@# loads — WITHOUT it mypy crashes at plugin load and reports a garbage count (luxlint --preflight
	@# flags this; FLEET-ONBOARDING-STANDARD §"mypy tail is HONEST"). Tools are installed FRESH on a
	@# lean base each run, never inherited from :dev (FLEET-BUILD-DEPLOY-STANDARD).
	@# Layout: app/ package at the repo root (fleet standard) — mypy targets `app` from /repo
	@# with the repo root on the path (no MYPYPATH crutch; the src/app migration is done, LUXUPT-65).
	@set +e; \
	docker run --rm -v $(PWD):/repo $(LUXLINT_IMAGE); ruff=$$?; \
	docker run --rm -v $(PWD):/repo $(LUXLINT_IMAGE) --emit-config mypy > /tmp/luxlint.mypy.ini; \
	docker run --rm -v $(PWD):/repo -v /tmp/luxlint.mypy.ini:/cfg/mypy.ini:ro -w /repo $(LUXLINT_MYPY_IMAGE) \
	  sh -c 'pip install -q --disable-pip-version-check "mypy>=2.3.0" pydantic pydantic-settings && mypy --config-file /cfg/mypy.ini app'; mypy=$$?; \
	$(MAKE) --no-print-directory lint-js; eslint=$$?; \
	if [ $$ruff -ne 0 ] || [ $$mypy -ne 0 ] || [ $$eslint -ne 0 ]; then \
	  echo "lint FAILED (luxlint=$$ruff mypy=$$mypy eslint=$$eslint)"; exit 1; \
	fi

.PHONY: lint-js
lint-js: ## Run JavaScript linting checks
	@echo '$(BLUE)Running JavaScript linting checks...$(NC)'
	npm run lint:js

.PHONY: lint-js-fix
lint-js-fix: ## Fix JavaScript linting issues
	@echo '$(BLUE)Fixing JavaScript linting issues...$(NC)'
	npm run lint:js:fix

# Architecture guard (luxarch) — pinned. LUXARCH_REGISTRY comes from Makefile.local (gitignored);
# empty on a clean public clone (guard-version-check + the guard runs skip cleanly when unset).
LUXARCH_REGISTRY ?=
LUXARCH_VERSION  ?= 0.38.2
LUXARCH_IMAGE    ?= $(LUXARCH_REGISTRY)/luxardolabs/luxarch:$(LUXARCH_VERSION)

.PHONY: arch
arch: ## Architecture conformance via luxarch (pinned; reads .luxarch.toml)
	docker run --rm -v $(PWD):/repo $(LUXARCH_IMAGE)

# Dependency-vulnerability / SCA guard (luxaudit) — pinned; host from Makefile.local.
# Mount-only, no tail, no deps: reads poetry.lock and checks every pinned dep against the
# LIVE OSV+PyPA feed, so each run is current with no rebuild — no cron needed.
LUXAUDIT_VERSION ?= 0.1.11
LUXAUDIT_IMAGE   ?= $(LUXARCH_REGISTRY)/luxardolabs/luxaudit:$(LUXAUDIT_VERSION)

.PHONY: audit
audit: ## Scan pinned deps against the live vulnerability feed (luxaudit)
	docker run --rm -v $(PWD):/repo $(LUXAUDIT_IMAGE)

.PHONY: guard-version-check
guard-version-check: ## Pull each guard's :latest FIRST, then warn if a pin is behind (FLEET-BUILD-DEPLOY-STANDARD)
	@for g in "luxarch $(LUXARCH_VERSION)" "luxlint $(LUXLINT_VERSION)" "luxaudit $(LUXAUDIT_VERSION)"; do \
	  set -- $$g; name=$$1; pin=$$2; \
	  docker pull -q $(LUXARCH_REGISTRY)/luxardolabs/$$name:latest >/dev/null 2>&1 || true; \
	  latest=$$(docker run --rm $(LUXARCH_REGISTRY)/luxardolabs/$$name:latest --version 2>/dev/null | awk '{print $$2}'); \
	  if [ -n "$$latest" ] && [ "$$latest" != "$$pin" ]; then \
	    printf "⚠ %s pinned %s, latest %s — bump the pin (preview: --new-rules --since %s)\n" "$$name" "$$pin" "$$latest" "$$pin"; \
	  else printf "✓ %s %s (current)\n" "$$name" "$$pin"; fi; \
	done

.PHONY: onboard-check
onboard-check: ## Prove the repo is onboarded: all three guards ON + HONEST, NOT green (FLEET-ONBOARDING-STANDARD §5)
	@set +e; fail=0; \
	docker run --rm -v $(PWD):/repo $(LUXARCH_IMAGE)  --version  >/dev/null || { echo "luxarch not wired";  fail=1; }; \
	docker run --rm -v $(PWD):/repo $(LUXLINT_IMAGE)  --preflight            || { echo "mypy tail NOT honest (luxlint --preflight)"; fail=1; }; \
	docker run --rm -v $(PWD):/repo $(LUXAUDIT_IMAGE) 2>&1 | grep -q "scan could not run" && { echo "luxaudit can't scan — supply-chain blind"; fail=1; }; \
	docker run --rm -v $(PWD):/repo $(LUXLINT_IMAGE)  --version  >/dev/null || { echo "luxlint not wired";  fail=1; }; \
	docker run --rm -v $(PWD):/repo $(LUXAUDIT_IMAGE) --version  >/dev/null || { echo "luxaudit not wired"; fail=1; }; \
	[ $$fail -eq 0 ] && echo "onboard-check: all three guards on + honest ✓" || { echo "onboard-check FAILED"; exit 1; }

.PHONY: check
check: ## Run all fleet guards — arch + lint + lint-js + audit + test (each runs; fails at end if any fails)
	@# One recipe / set +e / fail-at-end (LUXPM-120): arch and lint are red today, so a plain
	@# prereq list would abort before the later steps ever ran. Capture each, report which broke.
	@set +e; \
	$(MAKE) --no-print-directory arch;    arch=$$?; \
	$(MAKE) --no-print-directory lint;    lint=$$?; \
	$(MAKE) --no-print-directory audit;   audit=$$?; \
	$(MAKE) --no-print-directory test;    test=$$?; \
	if [ $$arch -ne 0 ] || [ $$lint -ne 0 ] || [ $$audit -ne 0 ] || [ $$test -ne 0 ]; then \
	  echo "check FAILED (arch=$$arch lint=$$lint audit=$$audit test=$$test)"; exit 1; \
	fi

.PHONY: test
test: ## Run pytest (canonical luxlint config) IN DOCKER — deps from the lock, source over-mounted
	@# FLEET-BUILD-DEPLOY-STANDARD "Lint & test images": deps come from the LOCK (Dockerfile.test,
	@# layer-cached on pyproject/poetry.lock), source is over-mounted (never baked), pytest config is
	@# emitted by luxlint. Nothing runs on the host venv or is inherited from :dev. luxupt ships on
	@# sqlite (conftest uses a temp sqlite via DATABASE_DIR) — no external DB to spin up.
	@set +e; \
	docker run --rm -v $(PWD):/repo $(LUXLINT_IMAGE) --emit-config pytest > /tmp/luxlint.pytest.ini; \
	docker build -q -f Dockerfile.test -t $(TEST_DEPS_IMAGE) . >/dev/null || { echo "test image build failed"; exit 1; }; \
	docker run --rm -v $(PWD):/w -w /w -v /tmp/luxlint.pytest.ini:/cfg.ini:ro $(TEST_DEPS_IMAGE) \
	  sh -c 'PYTHONPATH=/w pytest -c /cfg.ini --rootdir=/w tests'; \
	exit $$?

.PHONY: test-coverage
test-coverage: ## Canonical pytest config + coverage IN DOCKER (coverage config stays in pyproject)
	@set +e; \
	docker run --rm -v $(PWD):/repo $(LUXLINT_IMAGE) --emit-config pytest > /tmp/luxlint.pytest.ini; \
	docker build -q -f Dockerfile.test -t $(TEST_DEPS_IMAGE) . >/dev/null || { echo "test image build failed"; exit 1; }; \
	docker run --rm -v $(PWD):/w -w /w -v /tmp/luxlint.pytest.ini:/cfg.ini:ro $(TEST_DEPS_IMAGE) \
	  sh -c 'PYTHONPATH=/w pytest -c /cfg.ini --rootdir=/w tests --cov=app --cov-report=term-missing'; \
	exit $$?

.PHONY: clean
clean: ## Clean build artifacts
	@echo '$(BLUE)Cleaning build artifacts...$(NC)'
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.pyc' -delete
	rm -rf .pytest_cache .coverage htmlcov .mypy_cache .ruff_cache
	rm -rf dist/ build/ *.egg-info

# ============================================================================
# Frontend / CSS Build Targets
# ============================================================================

.PHONY: npm-install
npm-install: ## Install npm dependencies for Tailwind CSS
	@echo '$(BLUE)Installing npm dependencies...$(NC)'
	npm install
	@echo '$(GREEN)npm dependencies installed!$(NC)'

.PHONY: css-build
css-build: ## Build Tailwind CSS (production, minified)
	@echo '$(BLUE)Building Tailwind CSS...$(NC)'
	npm run build:css
	@echo '$(GREEN)CSS build complete: app/web/static/css/compiled.css$(NC)'

.PHONY: css-dev
css-dev: ## Watch and rebuild Tailwind CSS on changes
	@echo '$(BLUE)Starting Tailwind CSS watch mode...$(NC)'
	npm run dev:css

.PHONY: frontend
frontend: npm-install css-build ## Install npm deps and build CSS

.PHONY: docker-setup
docker-setup: ## Set up Docker buildx
	@echo '$(BLUE)Setting up Docker buildx...$(NC)'
	docker buildx create --name $(PROJECT_NAME)-builder --use 2>/dev/null || true
	docker buildx inspect --bootstrap

.PHONY: docker-login-hub
docker-login-hub: ## Login to Docker Hub (uses DOCKER_HUB_TOKEN env var or prompts)
	@echo '$(BLUE)Logging in to Docker Hub...$(NC)'
	@if [ -n "$$DOCKER_HUB_TOKEN" ]; then \
		echo "$$DOCKER_HUB_TOKEN" | docker login -u $(DOCKER_HUB_USER) --password-stdin; \
	else \
		docker login -u $(DOCKER_HUB_USER); \
	fi
	@echo '$(GREEN)Docker Hub login successful!$(NC)'

.PHONY: docker-login-ghcr
docker-login-ghcr: ## Login to GitHub Container Registry
	@echo '$(BLUE)Logging in to GitHub Container Registry...$(NC)'
	@echo "$(GHCR_TOKEN)" | docker login ghcr.io -u $(GITHUB_USER) --password-stdin
	@echo '$(GREEN)GHCR login successful!$(NC)'

.PHONY: docker-pull-cache
docker-pull-cache: ## Pull previous image for cache
	@echo '$(BLUE)Pulling previous image for cache...$(NC)'
	@docker pull $(LOCAL_IMAGE):latest 2>/dev/null || echo "No previous image found for cache (this is normal for first builds)"

.PHONY: docker-build-local
docker-build-local: validate-version validate-structure docker-setup docker-pull-cache ## Build Docker image (local load, amd64 only for fast iteration)
	@echo '$(BLUE)Building Docker image for local use (amd64 only)...$(NC)'
	DOCKER_BUILDKIT=$(DOCKER_BUILDKIT) docker buildx build \
		--platform $(PLATFORM_DEV) \
		--build-arg BUILDKIT_INLINE_CACHE=1 \
		--cache-from $(LOCAL_IMAGE):latest \
		--build-arg VERSION=$(VERSION) \
		--build-arg BUILD_DATE=$(BUILD_DATE) \
		--build-arg REVISION=$(REVISION) \
		--label "org.opencontainers.image.created=$(BUILD_DATE)" \
		--label "org.opencontainers.image.version=$(VERSION)" \
		--label "org.opencontainers.image.title=LuxUPT" \
		--label "org.opencontainers.image.description=A Docker-based solution for creating time-lapse videos from UniFi Protect cameras" \
		--label "org.opencontainers.image.url=https://github.com/luxardolabs/luxupt" \
		--label "org.opencontainers.image.source=https://github.com/luxardolabs/luxupt" \
		--label "org.opencontainers.image.authors=luxardolabs" \
		-t $(LOCAL_IMAGE):$(VERSION) \
		--load \
		.
	@echo '$(GREEN)Docker build complete!$(NC)'

.PHONY: build-dev
build-dev: validate-version validate-structure ## Build the local luxupt:dev image from current source (for dev overlays)
	@echo '$(BLUE)Building luxupt:dev from current source...$(NC)'
	DOCKER_BUILDKIT=$(DOCKER_BUILDKIT) docker build \
		--build-arg VERSION=$(VERSION)-dev \
		--build-arg BUILD_DATE=$(BUILD_DATE) \
		--build-arg REVISION=$(REVISION) \
		-t luxupt:dev \
		.
	@echo '$(GREEN)Built luxupt:dev — dev overlays run this image. Re-run after code changes.$(NC)'

.PHONY: docker-push-local
docker-push-local: validate-version validate-structure docker-setup docker-pull-cache ## Build and push to local registry (multi-arch: amd64 + arm64)
	@echo '$(BLUE)Building and pushing to local registry (multi-arch)...$(NC)'
	DOCKER_BUILDKIT=$(DOCKER_BUILDKIT) docker buildx build \
		--platform $(PLATFORM) \
		--build-arg BUILDKIT_INLINE_CACHE=1 \
		--cache-from $(LOCAL_IMAGE):latest \
		--build-arg VERSION=$(VERSION) \
		--build-arg BUILD_DATE=$(BUILD_DATE) \
		--build-arg REVISION=$(REVISION) \
		--label "org.opencontainers.image.created=$(BUILD_DATE)" \
		--label "org.opencontainers.image.version=$(VERSION)" \
		--label "org.opencontainers.image.title=LuxUPT" \
		--label "org.opencontainers.image.description=A Docker-based solution for creating time-lapse videos from UniFi Protect cameras" \
		--label "org.opencontainers.image.url=https://github.com/luxardolabs/luxupt" \
		--label "org.opencontainers.image.source=https://github.com/luxardolabs/luxupt" \
		--label "org.opencontainers.image.authors=luxardolabs" \
		-t $(LOCAL_IMAGE):$(VERSION) \
		--push \
		.
	@echo '$(GREEN)Pushed $(LOCAL_IMAGE):$(VERSION)$(NC)'

.PHONY: docker-push-hub
docker-push-hub: validate-version validate-structure docker-setup docker-login-hub ## Build and push to Docker Hub (multi-arch: amd64 + arm64)
	@echo '$(BLUE)Pulling previous Docker Hub image for cache...$(NC)'
	@docker pull $(DOCKER_HUB_IMAGE):latest 2>/dev/null || echo "No previous image found for cache"
	@echo '$(BLUE)Building and pushing to Docker Hub (multi-arch)...$(NC)'
	DOCKER_BUILDKIT=$(DOCKER_BUILDKIT) docker buildx build \
		--platform $(PLATFORM) \
		--build-arg BUILDKIT_INLINE_CACHE=1 \
		--cache-from $(DOCKER_HUB_IMAGE):latest \
		--build-arg VERSION=$(VERSION) \
		--build-arg BUILD_DATE=$(BUILD_DATE) \
		--build-arg REVISION=$(REVISION) \
		--label "org.opencontainers.image.created=$(BUILD_DATE)" \
		--label "org.opencontainers.image.version=$(VERSION)" \
		--label "org.opencontainers.image.title=LuxUPT" \
		--label "org.opencontainers.image.description=A Docker-based solution for creating time-lapse videos from UniFi Protect cameras" \
		--label "org.opencontainers.image.url=https://github.com/luxardolabs/luxupt" \
		--label "org.opencontainers.image.source=https://github.com/luxardolabs/luxupt" \
		--label "org.opencontainers.image.authors=luxardolabs" \
		-t $(DOCKER_HUB_IMAGE):$(VERSION) \
		--push \
		.
	@echo '$(GREEN)Pushed $(DOCKER_HUB_IMAGE):$(VERSION)$(NC)'

.PHONY: docker-push-ghcr
docker-push-ghcr: validate-version validate-structure docker-setup docker-login-ghcr ## Build and push to GHCR (multi-arch: amd64 + arm64)
	@echo '$(BLUE)Building and pushing to GHCR (multi-arch)...$(NC)'
	DOCKER_BUILDKIT=$(DOCKER_BUILDKIT) docker buildx build \
		--platform $(PLATFORM) \
		--cache-from type=registry,ref=$(GHCR_IMAGE):cache \
		--cache-to type=registry,ref=$(GHCR_IMAGE):cache,mode=max \
		--build-arg VERSION=$(VERSION) \
		--build-arg BUILD_DATE=$(BUILD_DATE) \
		--build-arg REVISION=$(REVISION) \
		--label "org.opencontainers.image.created=$(BUILD_DATE)" \
		--label "org.opencontainers.image.version=$(VERSION)" \
		--label "org.opencontainers.image.title=LuxUPT" \
		--label "org.opencontainers.image.description=A Docker-based solution for creating time-lapse videos from UniFi Protect cameras" \
		--label "org.opencontainers.image.url=https://github.com/luxardolabs/luxupt" \
		--label "org.opencontainers.image.source=https://github.com/luxardolabs/luxupt" \
		--label "org.opencontainers.image.authors=luxardolabs" \
		-t $(GHCR_IMAGE):$(VERSION) \
		--push \
		.
	@echo '$(GREEN)Pushed $(GHCR_IMAGE):$(VERSION)$(NC)'

.PHONY: docker-push-all
docker-push-all: docker-push-local docker-push-hub docker-push-ghcr ## Push to all registries (local, Docker Hub, GHCR)

.PHONY: docker-tag-latest-local
docker-tag-latest-local: ## Tag current version as latest (local)
	@echo '$(BLUE)Tagging $(VERSION) as latest in local registry...$(NC)'
	docker tag $(LOCAL_IMAGE):$(VERSION) $(LOCAL_IMAGE):latest
	docker push $(LOCAL_IMAGE):latest
	@echo '$(GREEN)Tagged and pushed latest to local registry$(NC)'

.PHONY: docker-tag-latest-hub
docker-tag-latest-hub: docker-login-hub ## Tag current version as latest (Docker Hub, multi-arch)
	@echo '$(BLUE)Tagging $(VERSION) as latest on Docker Hub...$(NC)'
	DOCKER_BUILDKIT=$(DOCKER_BUILDKIT) docker buildx build \
		--platform $(PLATFORM) \
		--build-arg BUILDKIT_INLINE_CACHE=1 \
		--cache-from $(DOCKER_HUB_IMAGE):$(VERSION) \
		--build-arg VERSION=$(VERSION) \
		--build-arg BUILD_DATE=$(BUILD_DATE) \
		--build-arg REVISION=$(REVISION) \
		--label "org.opencontainers.image.created=$(BUILD_DATE)" \
		--label "org.opencontainers.image.version=$(VERSION)" \
		--label "org.opencontainers.image.title=LuxUPT" \
		--label "org.opencontainers.image.description=A Docker-based solution for creating time-lapse videos from UniFi Protect cameras" \
		--label "org.opencontainers.image.url=https://github.com/luxardolabs/luxupt" \
		--label "org.opencontainers.image.source=https://github.com/luxardolabs/luxupt" \
		--label "org.opencontainers.image.authors=luxardolabs" \
		-t $(DOCKER_HUB_IMAGE):latest \
		--push \
		.
	@echo '$(GREEN)Tagged and pushed latest to Docker Hub$(NC)'

.PHONY: docker-tag-latest-ghcr
docker-tag-latest-ghcr: docker-login-ghcr ## Tag current version as latest (GHCR, multi-arch)
	@echo '$(BLUE)Tagging $(VERSION) as latest on GHCR...$(NC)'
	DOCKER_BUILDKIT=$(DOCKER_BUILDKIT) docker buildx build \
		--platform $(PLATFORM) \
		--build-arg BUILDKIT_INLINE_CACHE=1 \
		--cache-from $(GHCR_IMAGE):$(VERSION) \
		--build-arg VERSION=$(VERSION) \
		--build-arg BUILD_DATE=$(BUILD_DATE) \
		--build-arg REVISION=$(REVISION) \
		--label "org.opencontainers.image.created=$(BUILD_DATE)" \
		--label "org.opencontainers.image.version=$(VERSION)" \
		--label "org.opencontainers.image.title=LuxUPT" \
		--label "org.opencontainers.image.description=A Docker-based solution for creating time-lapse videos from UniFi Protect cameras" \
		--label "org.opencontainers.image.url=https://github.com/luxardolabs/luxupt" \
		--label "org.opencontainers.image.source=https://github.com/luxardolabs/luxupt" \
		--label "org.opencontainers.image.authors=luxardolabs" \
		-t $(GHCR_IMAGE):latest \
		--push \
		.
	@echo '$(GREEN)Tagged and pushed latest to GHCR$(NC)'

.PHONY: docker-tag-latest
docker-tag-latest: docker-tag-latest-local docker-tag-latest-hub docker-tag-latest-ghcr ## Tag as latest in all registries

.PHONY: release-hub
release-hub: docker-push-hub docker-tag-latest-hub ## Release to Docker Hub only: version + latest (multi-arch)
	@echo ''
	@echo '$(GREEN)========================================$(NC)'
	@echo '$(GREEN)Docker Hub Release $(VERSION) complete!$(NC)'
	@echo '$(GREEN)========================================$(NC)'
	@echo ''
	@echo '$(BLUE)Images published (amd64 + arm64):$(NC)'
	@echo '  $(DOCKER_HUB_IMAGE):$(VERSION)'
	@echo '  $(DOCKER_HUB_IMAGE):latest'
	@echo ''
	@echo '$(BLUE)Pull command:$(NC)'
	@echo '  docker pull $(DOCKER_HUB_IMAGE):latest'

.PHONY: release-ghcr
release-ghcr: docker-push-ghcr docker-tag-latest-ghcr ## Release to GHCR only: version + latest (multi-arch)
	@echo ''
	@echo '$(GREEN)========================================$(NC)'
	@echo '$(GREEN)GHCR Release $(VERSION) complete!$(NC)'
	@echo '$(GREEN)========================================$(NC)'
	@echo ''
	@echo '$(BLUE)Images published (amd64 + arm64):$(NC)'
	@echo '  $(GHCR_IMAGE):$(VERSION)'
	@echo '  $(GHCR_IMAGE):latest'
	@echo ''
	@echo '$(BLUE)Pull command:$(NC)'
	@echo '  docker pull $(GHCR_IMAGE):latest'

.PHONY: release
release: docker-push-all docker-tag-latest ## Full release: version + latest to all registries (multi-arch)
	@echo ''
	@echo '$(GREEN)========================================$(NC)'
	@echo '$(GREEN)Release $(VERSION) complete!$(NC)'
	@echo '$(GREEN)========================================$(NC)'
	@echo ''
	@echo '$(BLUE)Images published (amd64 + arm64):$(NC)'
	@echo ''
	@echo '  $(YELLOW)Local Registry:$(NC)'
	@echo '    $(LOCAL_IMAGE):$(VERSION)'
	@echo '    $(LOCAL_IMAGE):latest'
	@echo ''
	@echo '  $(YELLOW)Docker Hub:$(NC)'
	@echo '    $(DOCKER_HUB_IMAGE):$(VERSION)'
	@echo '    $(DOCKER_HUB_IMAGE):latest'
	@echo ''
	@echo '  $(YELLOW)GHCR:$(NC)'
	@echo '    $(GHCR_IMAGE):$(VERSION)'
	@echo '    $(GHCR_IMAGE):latest'
	@echo ''
	@echo '$(BLUE)Pull commands:$(NC)'
	@echo '  docker pull $(DOCKER_HUB_IMAGE):latest'
	@echo '  docker pull $(GHCR_IMAGE):latest'

.PHONY: run
run: ## Run the application locally with Poetry
	@echo '$(BLUE)Running application...$(NC)'
	$(POETRY) run python app/main.py

.PHONY: run-docker
run-docker: ## Run the application in Docker
	@echo '$(BLUE)Running in Docker...$(NC)'
	docker run --rm -it \
		-e UNIFI_PROTECT_API_KEY="your_key_here" \
		-e UNIFI_PROTECT_BASE_URL="https://your-protect/proxy/protect/integration/v1" \
		-v $(PWD)/output:/app/luxupt/output \
		$(LOCAL_IMAGE):$(VERSION)

.PHONY: shell
shell: ## Open a shell in the Docker container
	@echo '$(BLUE)Opening shell in container...$(NC)'
	docker run --rm -it \
		-v $(PWD)/output:/app/luxupt/output \
		--entrypoint /bin/sh \
		$(LOCAL_IMAGE):$(VERSION)

.PHONY: logs
logs: ## Show Docker logs
	@docker logs -f luxupt 2>&1 || echo "Container not running"

.PHONY: network
network: ## Create luxupt-network Docker network (if not exists)
	@echo '$(BLUE)Creating luxupt-network...$(NC)'
	@docker network create luxupt-network 2>/dev/null || echo "Network luxupt-network already exists"
	@echo '$(GREEN)Network ready!$(NC)'


.PHONY: info
info: validate-version ## Show project information
	@echo '$(BLUE)Project Information:$(NC)'
	@echo '  Name: $(PROJECT_NAME)'
	@echo '  Version: $(VERSION)'
	@echo '  Directory: $(CURRENT_DIR)'
	@echo '  Build Date: $(BUILD_DATE)'
	@echo '  Platform: $(PLATFORM)'
	@echo ''
	@echo '$(BLUE)Registry Information:$(NC)'
	@echo '  Local: $(LOCAL_IMAGE)'
	@echo '  Docker Hub: $(DOCKER_HUB_IMAGE)'
	@echo ''
	@echo '$(BLUE)Poetry Information:$(NC)'
	@$(POETRY) --version || echo "Poetry not installed"

# =============================================================================
# Production deploy — Site on ll01 (remote over SSH)
# -----------------------------------------------------------------------------
# repo prod/ is the SOURCE OF TRUTH; prod-sync pushes it, prod-deploy runs it.
# Prod keeps its OWN tiered storage (NVMe /mnt/docker + NAS videos) — NEVER the
# dev NFS. BB prod lives on a different server and is out of scope here.
# Release flow:  make docker-push-ghcr  ->  (bump prod/compose.yaml default tag)
#                ->  make prod-sync  ->  make prod-deploy
# =============================================================================
PROD_NODE ?= ll01
PROD_DIR  ?= /opt/luxupt
PROD_PORT ?= 8888
PROD_SSH  := ssh -o BatchMode=yes $(PROD_NODE)

.PHONY: prod-init
prod-init: ## [PROD] One-time: create tiered storage dirs (uid 1000) + network + $(PROD_DIR) on ll01
	@echo '$(BLUE)Preparing $(PROD_NODE)...$(NC)'
	@$(PROD_SSH) 'set -e; \
	  install -d -o 1000 -g 1000 /mnt/docker/luxupt/site/data /mnt/docker/luxupt/site/images /mnt/docker/luxupt/site/thumbnails; \
	  install -d -o 1000 -g 1000 /mnt/nas.internal/video/Timelapse/site; \
	  docker network inspect luxupt-network >/dev/null 2>&1 || docker network create luxupt-network; \
	  mkdir -p $(PROD_DIR)'
	@echo '$(GREEN)Prod host ready.$(NC)'

.PHONY: prod-sync
prod-sync: ## [PROD] Push repo prod/ (compose + nginx) to ll01:$(PROD_DIR) (repo is source of truth)
	@echo '$(BLUE)Syncing prod/ -> $(PROD_NODE):$(PROD_DIR)...$(NC)'
	rsync -az --delete --exclude data --exclude backups prod/ $(PROD_NODE):$(PROD_DIR)/
	@echo '$(GREEN)Synced.$(NC)'

.PHONY: prod-deploy
prod-deploy: ## [PROD] Pull image + (re)start on ll01; optional TAG=x.y.z (migrations run on start)
	@echo '$(BLUE)Deploying site on $(PROD_NODE) (tag: $(or $(TAG),default))...$(NC)'
	@$(PROD_SSH) 'cd $(PROD_DIR) && LUXUPT_TAG=$(TAG) docker compose pull && LUXUPT_TAG=$(TAG) docker compose up -d'
	@echo '$(GREEN)Deployed. Verify: make prod-health$(NC)'

.PHONY: prod-rollback
prod-rollback: ## [PROD] Redeploy a specific tag: make prod-rollback TAG=1.1.4
	@[ -n "$(TAG)" ] || { echo '$(RED)Set TAG=x.y.z (a tag present on ghcr)$(NC)'; exit 1; }
	@$(MAKE) --no-print-directory prod-deploy TAG=$(TAG)

.PHONY: prod-status
prod-status: ## [PROD] Show prod containers on ll01
	@$(PROD_SSH) 'cd $(PROD_DIR) && docker compose ps'

.PHONY: prod-logs
prod-logs: ## [PROD] Follow prod logs on ll01
	@$(PROD_SSH) 'cd $(PROD_DIR) && docker compose logs -f --tail=100'

.PHONY: prod-health
prod-health: ## [PROD] Hit site /health/live via nginx on ll01
	@$(PROD_SSH) 'curl -sk -o /dev/null -w "site :$(PROD_PORT) -> HTTP %{http_code}\n" https://localhost:$(PROD_PORT)/health/live'

.PHONY: prod-backup
prod-backup: ## [PROD] SQLite online-backup of the site DB on ll01 -> $(PROD_DIR)/backups/YYYY/MM/DD/
	@echo '$(BLUE)Backing up site DB on $(PROD_NODE)...$(NC)'
	@$(PROD_SSH) 'set -e; ts=$$(date +%Y%m%d-%H%M%S); dir=$(PROD_DIR)/backups/$$(date +%Y/%m/%d); mkdir -p $$dir; \
	  docker exec luxupt-site python -c "import sqlite3,sys; s=sqlite3.connect(\"/app/luxupt/output/timelapse.db\"); d=sqlite3.connect(\"/app/luxupt/output/.backup-$$ts.db\"); s.backup(d); d.close(); s.close()"; \
	  mv /mnt/docker/luxupt/site/data/.backup-$$ts.db $$dir/site_$$ts.db; \
	  ls -lh $$dir/site_$$ts.db'
	@echo '$(GREEN)Backup complete.$(NC)'