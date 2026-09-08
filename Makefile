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
CREATED := $(shell date -u +"%Y-%m-%dT%H:%M:%SZ")
# Git revision for OCI image provenance (repo.oci_image_labels)
BUILD_COMMIT := $(shell git -c safe.directory=$(CURRENT_DIR) rev-parse --short HEAD 2>/dev/null || echo unknown)
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
POETRY_IN_DOCKER_VERSION := 2.4.1
define poetry_docker
	docker run --rm -v $(PWD):/app -w /app python:3.14-slim \
	  sh -c "pip install -q poetry==$(POETRY_IN_DOCKER_VERSION) && poetry $(1)"
endef

.PHONY: poetry-lock
poetry-lock: ## Regenerate poetry.lock in a container (no local poetry needed)
	@echo '$(BLUE)poetry lock (in docker)...$(NC)'
	$(call poetry_docker,lock)

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
LUXLINT_VERSION ?= 0.45.1
LUXLINT_IMAGE   ?= $(LUXARCH_REGISTRY)/luxardolabs/luxlint:$(LUXLINT_VERSION)
# Pytest deps come from the lock via Dockerfile.test (used by make test).
TEST_DEPS_IMAGE    ?= luxupt-test-deps

.PHONY: format
format: ## Apply every canonical formatter in place -- Python (ruff) AND markdown (mdformat-gfm)
	@echo '$(BLUE)Formatting (luxlint --format)...$(NC)'
	@# The ONE canonical fixer. Do NOT hand-roll the ruff legs via --entrypoint ruff: that skips
	@# the MARKDOWN leg entirely (docs.markdown_format then reds with no way to fix it through
	@# make), and a bare `ruff format` would use ruff's DEFAULT line-length since the repo carries
	@# no ruff config. Never a bare mdformat either -- without mdformat-gfm it COLLAPSES GFM
	@# tables. Un-autofixable reds that remain are the burn-down, not a format failure.
	docker run --rm -v $(PWD):/repo $(LUXLINT_IMAGE) --format

.PHONY: lint
lint: ## luxlint ruff + eslint — MOUNT-ONLY (FLEET-MAKEFILE-STANDARD: lint and mypy are separate gate steps)
	@docker run --rm -v $(PWD):/repo $(LUXLINT_IMAGE)

.PHONY: mypy
mypy: ## mypy — MOUNT-ONLY (fleet typed deps baked); applies the [mypy].baseline ratchet itself
	@# No dev image / --emit-config / pip install: luxlint --mypy reads [source].paths and
	@# auto-injects the pydantic plugin.
	@docker run --rm -v $(PWD):/repo $(LUXLINT_IMAGE) --mypy

# JS linting is the luxlint image's job now (lint.eslint, canonical config). To auto-fix locally:
#   luxlint --emit-config eslint > .luxlint.eslint.config.mjs   # gitignored
#   npx eslint --config .luxlint.eslint.config.mjs app/web/static/js/ --fix

# Architecture guard (luxarch) — pinned. LUXARCH_REGISTRY comes from Makefile.local (gitignored);
# empty on a clean public clone (guard-version-check + the guard runs skip cleanly when unset).
LUXARCH_REGISTRY ?=
LUXARCH_VERSION  ?= 0.155.2
LUXARCH_IMAGE    ?= $(LUXARCH_REGISTRY)/luxardolabs/luxarch:$(LUXARCH_VERSION)

.PHONY: arch
arch: ## Architecture conformance via luxarch (pinned; reads .luxarch.toml)
	docker run --rm -v $(PWD):/repo $(LUXARCH_IMAGE)

# Dependency-vulnerability / SCA guard (luxaudit) — pinned; host from Makefile.local.
# Mount-only, no tail, no deps: reads poetry.lock and checks every pinned dep against the
# LIVE OSV+PyPA feed, so each run is current with no rebuild — no cron needed.
LUXAUDIT_VERSION ?= 0.4.0
LUXAUDIT_IMAGE   ?= $(LUXARCH_REGISTRY)/luxardolabs/luxaudit:$(LUXAUDIT_VERSION)

.PHONY: audit
audit: ## Scan pinned deps against the live vulnerability feed (luxaudit)
	docker run --rm -v $(PWD):/repo $(LUXAUDIT_IMAGE)

.PHONY: guard-version-check
guard-version-check: ## FATAL: fail the gate if any guard pin is behind the published latest
	@# Behind is a RED, not a warning (FLEET-MAKEFILE-STANDARD). A warn-only check is how an agent
	@# sat on a stale guard and worked against rules/conduct it never saw. `make guard-upgrade` clears it.
	@rc=0; for g in "luxarch $(LUXARCH_VERSION)" "luxlint $(LUXLINT_VERSION)" "luxaudit $(LUXAUDIT_VERSION)"; do \
	  set -- $$g; name=$$1; pin=$$2; \
	  docker pull -q $(LUXARCH_REGISTRY)/luxardolabs/$$name:latest >/dev/null 2>&1 || true; \
	  latest=$$(docker run --rm $(LUXARCH_REGISTRY)/luxardolabs/$$name:latest --version 2>/dev/null | awk '{print $$2}'); \
	  if [ -n "$$latest" ] && [ "$$latest" != "$$pin" ]; then \
	    printf "✗ %s pinned %s, latest %s — BEHIND. Preview: --new-rules --since %s; then make guard-upgrade\n" "$$name" "$$pin" "$$latest" "$$pin"; rc=1; \
	  else printf "✓ %s %s (current)\n" "$$name" "$$pin"; fi; \
	done; exit $$rc

.PHONY: guard-upgrade
guard-upgrade: ## Bump every guard pin to the published latest (prints what newly bites)
	@for g in luxarch luxlint luxaudit; do \
	  docker pull -q $(LUXARCH_REGISTRY)/luxardolabs/$$g:latest >/dev/null 2>&1 || true; \
	  latest=$$(docker run --rm $(LUXARCH_REGISTRY)/luxardolabs/$$g:latest --version 2>/dev/null | awk '{print $$2}'); \
	  [ -z "$$latest" ] && continue; \
	  var=$$(echo $$g | tr a-z A-Z)_VERSION; \
	  old=$$(sed -n "s/^$$var *?= *//p" Makefile); \
	  sed -i "s|^$$var\( *\)?= .*|$$var\1?= $$latest|" Makefile; \
	  if [ "$$g" = luxarch ] && [ -n "$$old" ] && [ "$$old" != "$$latest" ]; then \
	    docker run --rm $(LUXARCH_REGISTRY)/luxardolabs/luxarch:$$latest --new-rules --since $$old || true; \
	  fi; \
	done; echo "pins bumped — re-run make check"

.PHONY: onboard-check
onboard-check: ## Prove the repo is onboarded: all three guards ON + HONEST, NOT green (FLEET-ONBOARDING-STANDARD §5)
	@set +e; fail=0; \
	docker run --rm -v $(PWD):/repo $(LUXARCH_IMAGE)  --version  >/dev/null || { echo "luxarch not wired";  fail=1; }; \
	docker run --rm -v $(PWD):/repo $(LUXLINT_IMAGE)  --preflight            || { echo "mypy tail NOT honest (luxlint --preflight)"; fail=1; }; \
	docker run --rm -v $(PWD):/repo $(LUXAUDIT_IMAGE) 2>&1 | grep -q "scan could not run" && { echo "luxaudit can't scan — supply-chain blind"; fail=1; }; \
	docker run --rm -v $(PWD):/repo $(LUXLINT_IMAGE)  --version  >/dev/null || { echo "luxlint not wired";  fail=1; }; \
	docker run --rm -v $(PWD):/repo $(LUXAUDIT_IMAGE) --version  >/dev/null || { echo "luxaudit not wired"; fail=1; }; \
	[ $$fail -eq 0 ] && echo "onboard-check: all three guards on + honest ✓" || { echo "onboard-check FAILED"; exit 1; }

# The migration chain must BUILD the schema. `make test` builds its schema from create_all, so it
# structurally CANNOT catch a broken chain: `alembic upgrade head` could fail on its first revision
# while the suite stays green for months (LUXTASTE-285). db-verify migrates a FRESH EMPTY database to
# head for real, then diffs the result against the models and fails on ANY structural diff -- additive
# included, because here a missing table shows up as `add_table` and a destructive-only check would
# wave it through. Verifier: scripts/verify_migration_chain.py (luxarch --emit migration-chain).
VERIFY_DB_DIR      ?= /tmp/luxupt-verify-db
VERIFY_DATABASE_URL ?= sqlite+aiosqlite:///$(VERIFY_DB_DIR)/timelapse.db

.PHONY: db-verify
db-verify: ## FRESH EMPTY DB -> alembic upgrade head -> diff vs models (fails on ANY structural diff)
	@rm -rf $(VERIFY_DB_DIR); mkdir -p $(VERIFY_DB_DIR)
	@set -e; \
	docker build -q -f Dockerfile.test -t $(TEST_DEPS_IMAGE) . >/dev/null; \
	docker run --rm -v $(PWD):/w -w /w -v $(VERIFY_DB_DIR):$(VERIFY_DB_DIR) \
	  --env DATABASE_DIR=$(VERIFY_DB_DIR) --env VERIFY_DATABASE_URL=$(VERIFY_DATABASE_URL) \
	  $(TEST_DEPS_IMAGE) sh -c 'cd /w/app && PYTHONPATH=/w alembic upgrade head && cd /w && PYTHONPATH=/w python scripts/verify_migration_chain.py'; \
	status=$$?; rm -rf $(VERIFY_DB_DIR); exit $$status

.PHONY: honest
honest: ## A green check must MEAN nothing was silently unchecked (luxarch 0.114.0)
	@# --assert-scans fails ONLY when a rule family inspected ZERO files (never on reds), and
	@# --preflight proves the mypy verdict is honest. This lived in onboard-check, which nobody ran,
	@# so `check` could go green while a guard family was blind. Placed early in `check` so a later
	@# red step can never skip it. A no-op target named `honest` is itself flagged -- the recipe
	@# must really invoke --assert-scans.
	@docker run --rm -v $(PWD):/repo $(LUXARCH_IMAGE) --assert-scans
	@docker run --rm -v $(PWD):/repo $(LUXLINT_IMAGE) --preflight

# Regenerate the committed guard-status files. The fleet READS these instead of re-running every
# guard on every repo; freshness is verified against HEAD on read, so a stale file is detected, not
# trusted. The guards are read-only on /repo (load-bearing: a guard must never mutate what it
# judges), so --json stays a pure stdout primitive and this recipe does the stamping.
STAMP = python3 -c 'import json,sys,os; d=json.load(open(sys.argv[1])); d["commit"]=os.environ["SHA"]; d["generated_at"]=os.environ["TS"]; json.dump(d,open(sys.argv[2],"w"),indent=2)'

.PHONY: status
status: ## Regenerate committed guard-status files (.lux*-status.json) — commit them
	@# set -e: a failed stamp (empty/invalid --json) ABORTS -- never a false "wrote".
	@# || true: --json exits non-zero when the repo is RED, and a red repo still has a valid,
	@# committable status. The verdict lives IN the json.
	@set -e; export SHA=$$(git rev-parse HEAD) TS=$$(date -u +%FT%TZ); \
	docker run --rm -v $(PWD):/repo $(LUXLINT_IMAGE)  --json > /tmp/lux.json || true; $(STAMP) /tmp/lux.json .luxlint-status.json; \
	docker run --rm -v $(PWD):/repo $(LUXARCH_IMAGE)  --json > /tmp/lux.json || true; $(STAMP) /tmp/lux.json .luxarch-status.json; \
	docker run --rm -v $(PWD):/repo $(LUXAUDIT_IMAGE) --json > /tmp/lux.json || true; $(STAMP) /tmp/lux.json .luxaudit-status.json; \
	echo "wrote .lux*-status.json at $$SHA — commit them"

.PHONY: check
check: guard-version-check honest lint mypy test db-verify arch audit gitleaks ## THE fleet gate — byte-identical composition
	@# FLEET-MAKEFILE-STANDARD: ONE gate, this exact step list, in this order (pin drift -> ruff ->
	@# types -> tests-with-DB -> architecture -> dependency CVEs -> secrets). A gate missing any step
	@# is a HOLLOW gate: it ships an unchecked class and still says green. Do not re-order or drop.
	@# `check` is a GATE (prereq list -> stops at the first failure), not a report. For the whole red
	@# board at once, phase-ordered and file-clustered, run: make plan
	@echo '$(GREEN)check: all gate steps passed$(NC)'

.PHONY: plan
plan: ## The full architecture red board at once (phase-ordered) — the burn-down view, not the gate
	@docker run --rm -v $(PWD):/repo $(LUXARCH_IMAGE) --plan

# The throwaway test database. luxupt ships on SQLITE, so the "disposable container" the
# standard describes for a Postgres app is a disposable DIRECTORY here — same invariant: `make test`
# creates it, the suite runs against it, and it is wiped after, so the suite can never touch the dev
# database and a skipped DB suite can never read as a pass (repo.makefile_test_db_harness).
TEST_DB_DIR      ?= /tmp/luxupt-test-db
TEST_DATABASE_URL ?= sqlite+aiosqlite:///$(TEST_DB_DIR)/timelapse.db

.PHONY: test-db-up
test-db-up: ## Create the disposable test database (wipes any previous one)
	@rm -rf $(TEST_DB_DIR)
	@mkdir -p $(TEST_DB_DIR)

.PHONY: test-db-down
test-db-down: ## Stop + WIPE the disposable test database
	@rm -rf $(TEST_DB_DIR)

.PHONY: test
test: test-db-up ## Full pytest incl durability, against the throwaway DB — IN DOCKER
	@# FLEET-BUILD-DEPLOY-STANDARD "Lint & test images": deps come from the LOCK (Dockerfile.test,
	@# layer-cached on pyproject/poetry.lock), source is over-mounted (never baked), pytest config is
	@# emitted by luxlint. Nothing runs on the host venv or is inherited from :dev.
	@set +e; \
	docker run --rm -v $(PWD):/repo $(LUXLINT_IMAGE) --emit-config pytest > /tmp/luxlint.pytest.ini; \
	docker build -q -f Dockerfile.test -t $(TEST_DEPS_IMAGE) . >/dev/null || { echo "test image build failed"; exit 1; }; \
	docker run --rm -v $(PWD):/w -w /w -v /tmp/luxlint.pytest.ini:/cfg.ini:ro \
	  -v $(TEST_DB_DIR):$(TEST_DB_DIR) --env TEST_DATABASE_URL=$(TEST_DATABASE_URL) \
	  $(TEST_DEPS_IMAGE) sh -c 'PYTHONPATH=/w pytest -c /cfg.ini --rootdir=/w tests'; \
	status=$$?; $(MAKE) --no-print-directory test-db-down; exit $$status

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

# ONE shared fleet buildx builder -- never a per-project <repo>-builder. A per-project
# builder holds a completely separate cache (no base-layer sharing, its own pip/npm cache
# mounts, unbounded growth); ten repos = ten copies of the same base layers. The shared
# builder also gives cross-project cache hits. GC-capped via ~/.docker/buildkitd.toml.
# (FLEET-BUILD-DEPLOY-STANDARD -- repo.shared_buildx_builder / repo.buildx_builder_gc_capped)
BUILDX_BUILDER ?= luxardo-builder

.PHONY: docker-setup
docker-setup: ## Set up the shared fleet Docker buildx builder (create-once, GC-capped)
	@echo '$(BLUE)Setting up Docker buildx ($(BUILDX_BUILDER))...$(NC)'
	@docker buildx inspect $(BUILDX_BUILDER) >/dev/null 2>&1 || \
	  docker buildx create --name $(BUILDX_BUILDER) --driver docker-container --buildkitd-config $(HOME)/.docker/buildkitd.toml --use
	@docker buildx use $(BUILDX_BUILDER)
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
		--build-arg BUILD_VERSION=$(VERSION) \
		--build-arg BUILD_TIMESTAMP=$(CREATED) \
		--build-arg BUILD_COMMIT=$(BUILD_COMMIT) \
		--label "org.opencontainers.image.created=$(CREATED)" \
		--label "org.opencontainers.image.version=$(VERSION)" \
		--label "org.opencontainers.image.title=luxupt" \
		--label "org.opencontainers.image.description=A Docker-based solution for creating time-lapse videos from UniFi Protect cameras" \
		--label "org.opencontainers.image.url=https://github.com/luxardolabs/luxupt" \
		--label "org.opencontainers.image.source=https://github.com/luxardolabs/luxupt" \
		-t $(LOCAL_IMAGE):$(VERSION) \
		--load \
		.
	@echo '$(GREEN)Docker build complete!$(NC)'

.PHONY: build-dev
build-dev: validate-version validate-structure ## Build the local luxupt:dev image from current source (for dev overlays)
	@echo '$(BLUE)Building luxupt:dev from current source...$(NC)'
	DOCKER_BUILDKIT=$(DOCKER_BUILDKIT) docker build \
		--build-arg BUILD_VERSION=$(VERSION)-dev \
		--build-arg BUILD_TIMESTAMP=$(CREATED) \
		--build-arg BUILD_COMMIT=$(BUILD_COMMIT) \
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
		--build-arg BUILD_VERSION=$(VERSION) \
		--build-arg BUILD_TIMESTAMP=$(CREATED) \
		--build-arg BUILD_COMMIT=$(BUILD_COMMIT) \
		--label "org.opencontainers.image.created=$(CREATED)" \
		--label "org.opencontainers.image.version=$(VERSION)" \
		--label "org.opencontainers.image.title=luxupt" \
		--label "org.opencontainers.image.description=A Docker-based solution for creating time-lapse videos from UniFi Protect cameras" \
		--label "org.opencontainers.image.url=https://github.com/luxardolabs/luxupt" \
		--label "org.opencontainers.image.source=https://github.com/luxardolabs/luxupt" \
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
		--build-arg BUILD_VERSION=$(VERSION) \
		--build-arg BUILD_TIMESTAMP=$(CREATED) \
		--build-arg BUILD_COMMIT=$(BUILD_COMMIT) \
		--label "org.opencontainers.image.created=$(CREATED)" \
		--label "org.opencontainers.image.version=$(VERSION)" \
		--label "org.opencontainers.image.title=luxupt" \
		--label "org.opencontainers.image.description=A Docker-based solution for creating time-lapse videos from UniFi Protect cameras" \
		--label "org.opencontainers.image.url=https://github.com/luxardolabs/luxupt" \
		--label "org.opencontainers.image.source=https://github.com/luxardolabs/luxupt" \
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
		--build-arg BUILD_VERSION=$(VERSION) \
		--build-arg BUILD_TIMESTAMP=$(CREATED) \
		--build-arg BUILD_COMMIT=$(BUILD_COMMIT) \
		--label "org.opencontainers.image.created=$(CREATED)" \
		--label "org.opencontainers.image.version=$(VERSION)" \
		--label "org.opencontainers.image.title=luxupt" \
		--label "org.opencontainers.image.description=A Docker-based solution for creating time-lapse videos from UniFi Protect cameras" \
		--label "org.opencontainers.image.url=https://github.com/luxardolabs/luxupt" \
		--label "org.opencontainers.image.source=https://github.com/luxardolabs/luxupt" \
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
		--build-arg BUILD_VERSION=$(VERSION) \
		--build-arg BUILD_TIMESTAMP=$(CREATED) \
		--build-arg BUILD_COMMIT=$(BUILD_COMMIT) \
		--label "org.opencontainers.image.created=$(CREATED)" \
		--label "org.opencontainers.image.version=$(VERSION)" \
		--label "org.opencontainers.image.title=luxupt" \
		--label "org.opencontainers.image.description=A Docker-based solution for creating time-lapse videos from UniFi Protect cameras" \
		--label "org.opencontainers.image.url=https://github.com/luxardolabs/luxupt" \
		--label "org.opencontainers.image.source=https://github.com/luxardolabs/luxupt" \
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
		--build-arg BUILD_VERSION=$(VERSION) \
		--build-arg BUILD_TIMESTAMP=$(CREATED) \
		--build-arg BUILD_COMMIT=$(BUILD_COMMIT) \
		--label "org.opencontainers.image.created=$(CREATED)" \
		--label "org.opencontainers.image.version=$(VERSION)" \
		--label "org.opencontainers.image.title=luxupt" \
		--label "org.opencontainers.image.description=A Docker-based solution for creating time-lapse videos from UniFi Protect cameras" \
		--label "org.opencontainers.image.url=https://github.com/luxardolabs/luxupt" \
		--label "org.opencontainers.image.source=https://github.com/luxardolabs/luxupt" \
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

.PHONY: github-release
github-release: ## Tag v$(VERSION) and publish the GitHub Release carrying this version's notes
	@# A git tag is NOT a Release: without this the /releases page is empty and
	@# app/release_notes/$(VERSION).md never reaches anyone (repo.github_release_wired,
	@# FLEET-RELEASE-PROCESS §9). Fleet tag convention is v<VERSION> (repo.release_tag_hygiene).
	@test -f app/release_notes/$(VERSION).md || { \
	  echo "missing app/release_notes/$(VERSION).md — write the notes before releasing"; exit 1; }
	@git rev-parse "v$(VERSION)" >/dev/null 2>&1 || git tag -a "v$(VERSION)" -m "$(VERSION)"
	@git push origin "v$(VERSION)"
	@gh release create "v$(VERSION)" \
	  --title "$(VERSION)" \
	  --notes-file "app/release_notes/$(VERSION).md"

.PHONY: release
release: docker-push-all docker-tag-latest github-release ## Full release: images + the GitHub Release
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
	@echo '  Build Date: $(CREATED)'
	@echo '  Platform: $(PLATFORM)'
	@echo ''
	@echo '$(BLUE)Registry Information:$(NC)'
	@echo '  Local: $(LOCAL_IMAGE)'
	@echo '  Docker Hub: $(DOCKER_HUB_IMAGE)'
	@echo ''
	@echo '$(BLUE)Poetry Information:$(NC)'
	@$(POETRY) --version || echo "Poetry not installed"

# =============================================================================
# Production deploy (remote over SSH)
# -----------------------------------------------------------------------------
# repo prod/ is the SOURCE OF TRUTH; prod-sync pushes it, prod-deploy runs it.
# Prod keeps its OWN tiered storage (local NVMe + NAS videos) — NEVER the dev NFS.
# Release flow:  make docker-push-ghcr  ->  (bump prod/compose.yaml default tag)
#                ->  make prod-sync  ->  make prod-deploy
#
# This Makefile is COMMITTED to a PUBLIC repo, so it is environment-agnostic: every
# site value below is a variable with a placeholder default, and the real topology
# (node, site name, storage roots) lives in the gitignored Makefile.local.
# (FLEET-BUILD-DEPLOY-STANDARD — "Versioning your ops config: secrets vs topology".)
# =============================================================================
PROD_NODE     ?= prod-node
PROD_SITE     ?= site
PROD_DIR      ?= /opt/luxupt
PROD_PORT     ?= 8888
PROD_DATA_ROOT ?= /mnt/docker/luxupt
PROD_VIDEO_ROOT ?= /mnt/video/Timelapse
PROD_SSH  := ssh -o BatchMode=yes $(PROD_NODE)

.PHONY: prod-init
prod-init: ## [PROD] One-time: create tiered storage dirs (uid 1000) + network + $(PROD_DIR) on $(PROD_NODE)
	@echo '$(BLUE)Preparing $(PROD_NODE)...$(NC)'
	@$(PROD_SSH) 'set -e; \
	  install -d -o 1000 -g 1000 $(PROD_DATA_ROOT)/$(PROD_SITE)/data $(PROD_DATA_ROOT)/$(PROD_SITE)/images $(PROD_DATA_ROOT)/$(PROD_SITE)/thumbnails; \
	  install -d -o 1000 -g 1000 $(PROD_VIDEO_ROOT)/$(PROD_SITE); \
	  docker network inspect luxupt-network >/dev/null 2>&1 || docker network create luxupt-network; \
	  mkdir -p $(PROD_DIR)'
	@echo '$(GREEN)Prod host ready.$(NC)'

.PHONY: prod-sync
prod-sync: ## [PROD] Push repo prod/ (compose + nginx) to $(PROD_NODE):$(PROD_DIR) (repo is source of truth)
	@echo '$(BLUE)Syncing prod/ -> $(PROD_NODE):$(PROD_DIR)...$(NC)'
	rsync -az --delete --exclude data --exclude backups prod/ $(PROD_NODE):$(PROD_DIR)/
	@echo '$(GREEN)Synced.$(NC)'

.PHONY: prod-deploy
prod-deploy: ## [PROD] Pull image + (re)start on $(PROD_NODE); optional TAG=x.y.z (migrations run on start)
	@echo '$(BLUE)Deploying $(PROD_SITE) on $(PROD_NODE) (tag: $(or $(TAG),default))...$(NC)'
	@$(PROD_SSH) 'cd $(PROD_DIR) && LUXUPT_TAG=$(TAG) docker compose pull && LUXUPT_TAG=$(TAG) docker compose up -d'
	@echo '$(GREEN)Deployed. Verify: make prod-health$(NC)'

.PHONY: prod-rollback
prod-rollback: ## [PROD] Redeploy a specific tag: make prod-rollback TAG=1.1.4
	@[ -n "$(TAG)" ] || { echo '$(RED)Set TAG=x.y.z (a tag present on ghcr)$(NC)'; exit 1; }
	@$(MAKE) --no-print-directory prod-deploy TAG=$(TAG)

.PHONY: prod-status
prod-status: ## [PROD] Show prod containers on $(PROD_NODE)
	@$(PROD_SSH) 'cd $(PROD_DIR) && docker compose ps'

.PHONY: prod-logs
prod-logs: ## [PROD] Follow prod logs on $(PROD_NODE)
	@$(PROD_SSH) 'cd $(PROD_DIR) && docker compose logs -f --tail=100'

.PHONY: prod-health
prod-health: ## [PROD] Hit $(PROD_SITE) /health/live via nginx on $(PROD_NODE)
	@$(PROD_SSH) 'curl -sk -o /dev/null -w "$(PROD_SITE) :$(PROD_PORT) -> HTTP %{http_code}\n" https://localhost:$(PROD_PORT)/health/live'

.PHONY: prod-backup
prod-backup: ## [PROD] SQLite online-backup of the $(PROD_SITE) DB on $(PROD_NODE) -> $(PROD_DIR)/backups/YYYY/MM/DD/
	@echo '$(BLUE)Backing up $(PROD_SITE) DB on $(PROD_NODE)...$(NC)'
	@$(PROD_SSH) 'set -e; ts=$$(date +%Y%m%d-%H%M%S); dir=$(PROD_DIR)/backups/$$(date +%Y/%m/%d); mkdir -p $$dir; \
	  docker exec luxupt-$(PROD_SITE) python -c "import sqlite3,sys; s=sqlite3.connect(\"/app/luxupt/output/timelapse.db\"); d=sqlite3.connect(\"/app/luxupt/output/.backup-$$ts.db\"); s.backup(d); d.close(); s.close()"; \
	  mv $(PROD_DATA_ROOT)/$(PROD_SITE)/data/.backup-$$ts.db $$dir/$(PROD_SITE)_$$ts.db; \
	  ls -lh $$dir/$(PROD_SITE)_$$ts.db'
	@echo '$(GREEN)Backup complete.$(NC)'