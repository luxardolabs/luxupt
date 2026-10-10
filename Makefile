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

# The fleet assets (guard-upgrade, gitleaks) name the guard registry REGISTRY.
REGISTRY ?= $(LUXARCH_REGISTRY)

# Docker registry settings
LOCAL_REGISTRY ?=
DOCKER_HUB_USER := luxardolabs
GITHUB_USER := luxardolabs
LOCAL_IMAGE := $(LOCAL_REGISTRY)/$(DOCKER_HUB_USER)/$(PROJECT_NAME)
GHCR_IMAGE := ghcr.io/$(GITHUB_USER)/$(PROJECT_NAME)
# LOCAL inspection build — BARE on purpose (repo.image_name_declares_provenance): it is never
# pushed, so it must not wear a registry name a stack could pin and a prune could orphan.
LOCAL_BUILD_IMAGE := $(PROJECT_NAME):local

# Immutable deploy tags (luxarch --emit image-block v5) — the ONLY tags a stack may pin.
# CANDIDATE is pushed by the build and pinned by nothing: it exists so the exact bits can be
# scanned before any release tag points at them.
IMAGE           := $(LOCAL_IMAGE)
COMMIT          := $(shell git rev-parse --short=12 HEAD 2>/dev/null || echo unknown)
VERSION_IMAGE   := $(IMAGE):$(VERSION)
SHA_IMAGE       := $(IMAGE):sha-$(COMMIT)
CANDIDATE_IMAGE := $(IMAGE):candidate-$(COMMIT)
comma           := ,

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
BUILD_ARGS := --build-arg BUILD_VERSION=$(VERSION) --build-arg BUILD_TIMESTAMP=$(CREATED) --build-arg BUILD_COMMIT=$(BUILD_COMMIT)
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
	@if [ ! -f "app/core/config.py" ] || [ ! -f "app/clients/camera_manager.py" ] || [ ! -f "app/main.py" ]; then \
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
lock: poetry-lock  ## Regenerate poetry.lock (alias for poetry-lock — runs IN DOCKER)
	@:

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

# --- Secret scanning: the fleet privacy gate (content + commit identity) ---
.PHONY: gitleaks gitleaks-staged
# luxarch:gitleaks asset v11 - DO NOT edit this marker line; it is how repo.emitted_assets_current knows your copy is current. Re-emit with `luxarch --emit gitleaks`.
# ── The privacy gate: BOTH surfaces ─────────────────────────────────────────────────────────────
# Emitted by `luxarch --emit gitleaks`. Drop in verbatim.
#
# `gitleaks` scans DIFF CONTENT. A commit's author/committer address lives in the commit object
# HEADER and never appears in a patch, so no content rule can ever match it — it is a surface the
# scanner does not read. A repo reported `no leaks found` over 963 commits while 29 of them carried a
# personal address in both the author and committer fields, and it would have reported exactly the
# same thing after the scrub: identical output, opposite truth. Measured across the fleet, EIGHT
# repos carry a personal address in history and two of them are PUBLIC.

# Commit identities this repo accepts. The fleet account's `users.noreply.github.com` address, plus
# GitHub's own web-UI committer. Widen ONLY for a real outside contributor, with a comment saying who.
# NOT for the org account's real address: a role mailbox in commit metadata is published with every
# clone exactly like a personal one (six fleet repos carried it, one PUBLIC). Its
# omission here is the policy, not an oversight: the answer is the scrub printed below, and the
# repo's agent performs it once the OWNER approves the force-push.
# Anchored on the CLOSING BRACKET, because the compared line is `Name <email>` — not a bare
# address. The first cut allowed `^noreply@github.com$$`, which can NEVER match a
# `Name <email>` line, so the GitHub web-UI identity was silently DENIED and the canonical
# recipe would have refused on any repo carrying a web-UI commit. Measured across the fleet: it
# denied 4 of 6 distinct identity lines instead of the 3 real offenders.
# It was missed because the only repo it was tested on has no web-UI commits, so the broken
# branch never ran. The bracket also closes a substring hole: unanchored,
# `<x@users.noreply.github.com.attacker.test>` would have been allowed.
# v11: the noreply address is `<local@users.noreply.github.com>`, and the local part has no `@`. v10's
# `<[^>]*users…` admitted `<dev.real@gmail.com.users.noreply.github.com>`, a real address in the clear.
GIT_IDENTITY_OK ?= <[^@<> ]+@users\.noreply\.github\.com>$$|<noreply@github\.com>$$

# The secret scanner, PINNED and MIRRORED in the fleet registry. The fleet bans a moving tag
# everywhere it can see one, and this used to ship `ghcr.io/gitleaks/gitleaks:latest` inside the asset every
# repo adopts verbatim: the privacy gate could not run with ghcr unreachable or the local copy pruned, and
# nothing recorded which scanner said "no leaks found". New detection rules still arrive, through the fleet's
# own mechanism: luxarch bumps this pin in a release, and `repo.emitted_assets_current` tells you to re-emit.
# v5: the HOST is never written here. v4 inlined the private registry, so dropping
# this asset in "verbatim" put the host into a committed Makefile, and on a public repo the fleet's
# own gitleaks disclosure tier refused the commit. The mirror lives beside the guards, so the ref is
# derived from wherever this repo already pulls luxlint (`$(LUXLINT)`, which the scan below needs
# anyway). It works whichever variable holds your guard registry (REGISTRY, LUXARCH_REGISTRY, …).
# Recursive `=` so it resolves at use, whatever order LUXLINT is defined in.
# v9: PINNED BY DIGEST, and buildable off-network. The digest is the scanner's identity; the registry is
# only where it is fetched from. Beside a registry-qualified `$(LUXLINT)` it pulls the fleet mirror; with
# a local guard build (`luxlint:local`, on a machine with no access to the fleet registry, such as an
# airgapped laptop) it pulls the public image. v8 derived `./gitleaks:…` there, an unpullable reference, so the
# privacy gate could not run at all. The mirror and the public image share the digest, so both
# resolve to the same bits, and a tampered or re-tagged copy fails the pull instead of scanning.
GITLEAKS_IMAGE = $(if $(findstring /,$(LUXLINT)),$(dir $(LUXLINT)),zricethezav/)gitleaks:v8.30.1@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f

gitleaks: ## secret scan over FULL HISTORY + the commit-identity pass (the hooks cover commit/push)
	@set -e; C=$$(mktemp); trap 'rm -f "$$C"' EXIT INT TERM; \
	docker run --rm -v $(PWD):/repo $(LUXLINT) --emit-config gitleaks > "$$C"; \
	docker run --rm -v $(PWD):/repo -v "$$C":/gl.toml:ro -w /repo \
	  $(GITLEAKS_IMAGE) git /repo -c /gl.toml --redact -v
	@# The identity pass — the half gitleaks structurally cannot do. Cheap: one `git log`.
	@# Walks what THIS repo publishes (branches, tags, HEAD), NOT `--all`: a remote-tracking ref caches the
	@# remote's state, which during a scrub is by definition the un-rewritten history you are about to
	@# force-push over — `--all` refused the verified fix, and any `git fetch` re-armed it.
	@bad=$$(git log --branches --tags HEAD --pretty='%an <%ae>%n%cn <%ce>' 2>/dev/null | sort -u \
	  | grep -vE '$(GIT_IDENTITY_OK)' || true); \
	if [ -n "$$bad" ]; then \
	  echo "REFUSING: a non-fleet identity appears in commit METADATA (author/committer):"; \
	  echo "$$bad" | sed 's/^/    /'; \
	  echo "gitleaks cannot see this — it scans diffs, not commit headers, so it reported no leaks."; \
	  echo "An address here is attached to every affected commit forever, not to one line of one file."; \
	  echo "Scrub per FLEET-ONBOARDING-STANDARD §2: mirror backup -> git filter-repo -> re-verify with"; \
	  echo "  git log --branches --tags HEAD --pretty='%an <%ae>%n%cn <%ce>' | sort -u"; \
	  echo "-> ask the OWNER to approve the force-push, then do it yourself. Never force-push unapproved."; \
	  exit 1; \
	fi

# v6: the STAGED scan the commit hook calls (`hooks/pre-commit` → `make gitleaks-staged`) is part of the
# asset now. v5 shipped only the full-history half, so 9 of 10 adopting repos hand-wrote this target
# and the tenth had none, leaving its pre-commit hook pointing at a missing recipe. If your Makefile
# carries its own `gitleaks-staged`, delete it when you re-emit: this one replaces it.
# v7: `-w /repo` is LOAD-BEARING. Without it git runs outside the repo, falls back to `git diff
# --no-index`, rejects `--staged`, and gitleaks EXITS 0: v6 let a staged secret through while printing
# a git error (measured on a planted GitHub token: v6 exit 0, v7 "leaks found: 1" exit 1).
# v8: the denylist goes to a PER-RUN `mktemp` file, removed on exit. v7 wrote a fixed
# `/tmp/gl.toml` that outlived the run: on a host where commit and push run as different users, the
# next user's redirect was refused (`fs.protected_regular=1`, the Fedora default, blocks O_CREAT on
# another user's file in sticky /tmp even for root), so the privacy gate failed every commit or push
# after a user switch (2 of 2 measured). Two repos scanning at once also shared one file, so one could
# scan with the other's carve-outs. The full-history scan now also passes `-w /repo`, like the staged one.
gitleaks-staged: ## secret scan of the STAGED changes (run by hooks/pre-commit)
	@set -e; C=$$(mktemp); trap 'rm -f "$$C"' EXIT INT TERM; \
	docker run --rm -v $(PWD):/repo $(LUXLINT) --emit-config gitleaks > "$$C"; \
	docker run --rm -v $(PWD):/repo -v "$$C":/gl.toml:ro -w /repo \
	  $(GITLEAKS_IMAGE) protect --staged /repo -c /gl.toml --redact -v

# Code-style + type guard (luxlint) — pinned; host from Makefile.local ($(LUXARCH_REGISTRY)).
LUXLINT_VERSION := 0.62.2
LUXLINT_IMAGE   ?= $(LUXARCH_REGISTRY)/luxardolabs/luxlint:$(LUXLINT_VERSION)
LUXLINT = $(LUXLINT_IMAGE)
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
LUXARCH_VERSION   := 0.275.4
LUXARCH_IMAGE    ?= $(LUXARCH_REGISTRY)/luxardolabs/luxarch:$(LUXARCH_VERSION)

.PHONY: arch
arch: ## Architecture conformance via luxarch (pinned; reads .luxarch.toml)
	docker run --rm -v $(PWD):/repo $(LUXARCH_IMAGE)

# Dependency-vulnerability / SCA guard (luxaudit) — pinned; host from Makefile.local.
# Mount-only, no tail, no deps: reads poetry.lock and checks every pinned dep against the
# LIVE OSV+PyPA feed, so each run is current with no rebuild — no cron needed.
LUXAUDIT_VERSION := 0.13.1
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
# luxarch:guard-upgrade asset v1 - DO NOT edit this marker line; it is how repo.emitted_assets_current knows your copy is current. Re-emit with `luxarch --emit guard-upgrade`.
guard-upgrade:  ## Bump every guard pin to the published latest (prints what newly bites)
	@for g in luxarch luxlint luxaudit; do \
	  docker pull -q $(REGISTRY)/luxardolabs/$$g:latest >/dev/null 2>&1 || true; \
	  latest=$$(docker run --rm $(REGISTRY)/luxardolabs/$$g:latest --version 2>/dev/null | awk '{print $$2}'); \
	  var=$$(echo $$g | tr a-z A-Z)_VERSION; \
	  old=$$(sed -n -E "s/^$$var[[:space:]]*:=[[:space:]]*//p" Makefile); \
	  if [ -z "$$old" ]; then echo "!! no $$var pin found in Makefile — NOT bumped"; continue; fi; \
	  if [ -z "$$latest" ]; then echo "!! could not read $$g:latest — $$var left at $$old"; continue; fi; \
	  checked=1; \
	  sed -i -E "s|^($$var[[:space:]]*:=[[:space:]]*).*|\\1$$latest|" Makefile; \
	  new=$$(sed -n -E "s/^$$var[[:space:]]*:=[[:space:]]*//p" Makefile); \
	  if [ "$$new" != "$$latest" ]; then echo "!! $$var did NOT change (still $$new)"; exit 1; fi; \
	  if [ "$$old" != "$$latest" ]; then echo "$$var $$old -> $$latest"; bumped=1; fi; \
	  [ "$$g" = luxarch ] && [ "$$old" != "$$latest" ] && docker run --rm -v $(PWD):/repo $(REGISTRY)/luxardolabs/luxarch:$$latest --new-rules --since $$old || true; \
	done; \
	if [ -n "$$bumped" ]; then echo "pins bumped — re-run make check"; \
	elif [ -n "$$checked" ]; then echo "all pins already at latest"; \
	else echo "!! could not reach the registry — NO pin was checked; currency NOT established"; exit 1; fi

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
VERIFY_DB_DIR      ?= $(CURDIR)/.cache/luxupt-verify-db
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
	J=$$(mktemp); trap 'rm -f "$$J"' EXIT INT TERM; \
	docker run --rm -v $(PWD):/repo $(LUXLINT_IMAGE)  --json > "$$J" || true; $(STAMP) "$$J" .luxlint-status.json; \
	docker run --rm -e LUXARCH_STATUS_WRITE=1 -v $(PWD):/repo $(LUXARCH_IMAGE)  --json > "$$J" || true; $(STAMP) "$$J" .luxarch-status.json; \
	docker run --rm -v $(PWD):/repo $(LUXAUDIT_IMAGE) --json > "$$J" || true; $(STAMP) "$$J" .luxaudit-status.json; \
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

# ── Test image (the image block's verification half; this repo keeps its own site-aware deploy) ──
# LOCAL verification image — BARE on purpose: it cannot be pushed by accident and cannot be mistaken
# for a deployable. Rebuilt from source every run: the Dockerfile's `test` stage, which is
# production's app layers plus the dev group.
TEST_IMAGE := $(PROJECT_NAME):test

.PHONY: test-build
test-build: ## Build the LOCAL test image from source (never pushed, never a deploy tag)
	@docker build --load --target test $(BUILD_ARGS) -f Dockerfile -t $(TEST_IMAGE) . >/dev/null

# ── Test block settings (above the emitted block; its own lines are `?=` defaults) ─────────────
# luxupt ships on SQLite: there is no backing service to start. The suite's conftest creates its
# throwaway database in a fresh temp dir inside the container (test.db_isolated), so the run needs
# no service, no network and no database URL from here.
TEST_SERVICES :=
TEST_ENV      :=

.PHONY: test
# luxarch:test-block asset v1 - DO NOT edit this marker line; it is how repo.emitted_assets_current knows your copy is current. Re-emit with `luxarch --emit test-block`.
# ── Test: THE suite, in the test image, against an isolated stack of real services ───────────────
# Emitted by `luxarch --emit test-block`; paste below the image block (it uses TEST_IMAGE and
# test-build from there). Enforced by `repo.test_block_wired`. Settings are `?=` defaults: set them
# above this block. What the suite can see, and what only `make smoke` sees: --doc
# FLEET-MAKEFILE-STANDARD §1.
#
# Before this block every repo wrote its own `make test`: four repos, four ways (a lint image with
# the source mounted, the dev image with pytest pip-installed at run time, a repo script, a test
# image), each with its own readiness loop and coverage wiring, and a coverage pipe under make's
# /bin/sh that hid pytest's failure. This is the documented practice of the tools instead:
#   - ISOLATED STACK (Docker Compose): the backing services run in a compose project of their own,
#     one per run (`-p`), so parallel runs never collide, the suite cannot reach the dev database or
#     cache at all (it is on another network), and teardown (`down --volumes --remove-orphans`)
#     removes exactly this run's containers and data, pass or fail, never the dev stack.
#   - READINESS from each service's own compose healthcheck (`up --wait`), not a sleep or a loop:
#     the service declares when it is ready. For Postgres, probe over TCP with the real role
#     (`pg_isready -h 127.0.0.1 -U <user> -d <db>`); over the socket it answers while initdb's
#     temporary server is still up. `repo.test_stack_parity` checks these are the services prod runs.
#   - Ctrl-C stops the suite (`--init` forwards the signal; a shell as PID 1 ignores it).
#   - THE TEST IMAGE built from this source (`test-build`, the image's `--with dev` stage), with the
#     fleet's pytest config (`luxlint --emit-config pytest`: -ra, strict markers and config,
#     warnings are errors), readable by the image's non-root user. The source is mounted read-only
#     and pytest writes no cache into it.
#   - COVERAGE with coverage.py itself, not pytest-cov: `coverage run --branch` under the sysmon
#     core (fast branch coverage on Python 3.14), data in /tmp, then `coverage report` judged by
#     `luxlint --coverage-ratchet` against `[test].coverage_min` (off until you set a floor; it
#     only ratchets up). `coverage` belongs in the dev dependency group.
#   - BOTH EXIT CODES reach make: pytest's and the ratchet's. No pipe carries either. The ratchet
#     reads coverage's own report file, never the suite's output (a printed `TOTAL … 100%` or
#     pytest's `[100%]` would otherwise pass for a measurement), and a report that measured nothing
#     fails.
#   - Each run's project is named from its own `mktemp -d` token, and refuses to run without one (a
#     PID repeats across containers and CI runners), and everything mounts the makefile's directory ($(CURDIR)), so `make -C` runs the
#     right suite.

# Setting: the compose command, and the profile holding the test services --------------------
TEST_COMPOSE ?= docker compose
TEST_PROFILE ?= test
# Setting: the env file compose interpolates the file with (it reads EVERY service, so an app
# service's `${TAG:?}` needs a value even when only the test services start) -----------------
TEST_ENV_FILE ?= $(firstword $(wildcard .env.test .env.dev .env.example))
# Setting: the backing services the suite needs (each with a healthcheck); empty: none -----------
TEST_SERVICES ?= db-test
# Setting: the suite's environment: the test services' URLs, by service name on the test network -
TEST_ENV ?= -e TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@db-test:5432/postgres
# Setting: where the suite runs from (a monorepo's apps/backend), and every package coverage
# measures, comma-separated (`app,collector`): it replaces any [tool.coverage.run] source ---------
TEST_WORKDIR ?= .
TEST_COV ?= app
# Setting: NONE. For a one-off run only, on the command line: `make test PYTEST_ARGS='-k orders'`.
# A committed value narrows THE suite for everyone (`repo.test_block_wired` reds one) ---------
PYTEST_ARGS ?=

# One-off pytest arguments reach the container through the environment, never spliced into a quoted
# command line (`-k 'a or b'` would otherwise split it).
export PYTEST_ARGS

test: test-build ## THE suite: test image, an isolated stack of real services, coverage ratchet
	@set -u; \
	D=$$(mktemp -d); tok=$$(basename "$$D" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9'); \
	[ $${#tok} -ge 8 ] || { echo "REFUSING: could not make a unique name for this run"; rm -rf "$$D"; exit 2; }; \
	run="t$$(printf '%s' '$(notdir $(CURDIR))' | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9_-' '-')-test-$$tok"; \
	dc="$(TEST_COMPOSE) -p $$run $(if $(TEST_ENV_FILE),--env-file $(TEST_ENV_FILE)) --profile $(TEST_PROFILE)"; \
	chmod 777 "$$D"; \
	trap '$$dc down --volumes --remove-orphans >/dev/null 2>&1; rm -rf "$$D"' EXIT INT TERM; \
	net=; if [ -n "$(TEST_SERVICES)" ]; then \
	  $$dc up -d --wait --wait-timeout 120 $(TEST_SERVICES) \
	    || { echo "FAIL  the test services did not become healthy: $(TEST_SERVICES)"; exit 1; }; \
	  cid=$$($$dc ps -q $(firstword $(TEST_SERVICES))); net=; \
	  for n in $$(docker inspect -f '{{range $$k, $$v := .NetworkSettings.Networks}}{{$$k}} {{end}}' $$cid); do \
	    [ "$$(docker network inspect -f '{{index .Labels "com.docker.compose.project"}}' $$n)" = "$$run" ] && { net=$$n; break; }; done; \
	  [ -n "$$net" ] || { echo "FAIL  $(firstword $(TEST_SERVICES)) joined no network of this run's own project ($$run)"; exit 1; }; \
	  net="--network $$net"; fi; \
	docker run --rm -v $(CURDIR):/repo $(LUXLINT) --emit-config pytest > "$$D/pytest.ini" || exit 2; \
	chmod 644 "$$D/pytest.ini"; \
	docker run --rm --init $$net $(TEST_ENV) -e PYTEST_ADDOPTS="$${PYTEST_ARGS:-}" \
	  -e COVERAGE_CORE=sysmon -e COVERAGE_FILE=/out/.coverage -e PYTHONDONTWRITEBYTECODE=1 \
	  -v $(CURDIR):/repo:ro -v "$$D":/out -w /repo/$(TEST_WORKDIR) $(TEST_IMAGE) \
	  sh -c 'python -m coverage run --branch --source=$(TEST_COV) -m pytest -c /out/pytest.ini --rootdir=. -p no:cacheprovider; s=$$?; python -m coverage report --show-missing > /out/coverage.txt; echo $$? > /out/coverage.rc; cat /out/coverage.txt; exit $$s'; \
	rc=$$?; \
	[ "$$rc" = 0 ] || { echo "FAIL  the suite failed (exit $$rc)"; exit 1; }; \
	[ "$$(cat "$$D/coverage.rc" 2>/dev/null)" = 0 ] || { echo "FAIL  coverage measured nothing (coverage report: $$(tail -n 1 "$$D/coverage.txt" 2>/dev/null)): check TEST_COV names the package the suite imports"; exit 1; }; \
	docker run --rm -i -v $(CURDIR):/repo $(LUXLINT) --coverage-ratchet < "$$D/coverage.txt" > "$$D/ratchet.txt"; crc=$$?; \
	sed -n '/coverage ratchet/,$$p' "$$D/ratchet.txt"; \
	[ "$$crc" = 0 ] || { echo "FAIL  the coverage ratchet failed (its verdict is above): add tests, never lower the floor"; exit 1; }

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

# luxarch:css-watch asset v1 - DO NOT edit this marker line; it is how repo.emitted_assets_current knows your copy is current. Re-emit with `luxarch --emit css-watch`.
# Live stylesheet rebuilds for local development, with Node in a throwaway container: nothing is
# installed on the host and nothing runs in compose. The output it writes is gitignored; the image
# builds its own (luxarch --emit css-stage). See luxarch --doc FLEET-BUILD-DEPLOY-STANDARD.
CSS_NODE_IMAGE ?= node:24-slim

.PHONY: css-watch
css-watch: ## Recompile the stylesheet on change (Node in a throwaway container; output gitignored)
	docker run --rm -it -v "$(CURDIR)":/w -w /w $(CSS_NODE_IMAGE) \
	  sh -c 'npm ci --no-audit --no-fund && npm run build:css -- --watch'

# ONE shared fleet buildx builder -- never a per-project <repo>-builder. A per-project
# builder holds a completely separate cache (no base-layer sharing, its own pip/npm cache
# mounts, unbounded growth); ten repos = ten copies of the same base layers. The shared
# builder also gives cross-project cache hits. GC-capped via ~/.docker/buildkitd.toml.
# (FLEET-BUILD-DEPLOY-STANDARD -- repo.shared_buildx_builder / repo.buildx_builder_gc_capped)
.PHONY: buildx-setup
# luxarch:buildx-setup asset v2 - DO NOT edit this marker line; it is how repo.emitted_assets_current knows your copy is current. Re-emit with `luxarch --emit buildx-setup`.
BUILDX_BUILDER ?= luxardo-builder
buildx-setup:
	@mkdir -p $(HOME)/.docker
	@[ -f $(HOME)/.docker/buildkitd.toml ] || printf '[worker.oci]\n  gc = true\n  [[worker.oci.gcpolicy]]\n    keepBytes = "20GB"\n    all = true\n' > $(HOME)/.docker/buildkitd.toml
	@docker buildx inspect $(BUILDX_BUILDER) >/dev/null 2>&1 || \
	  docker buildx create --name $(BUILDX_BUILDER) --driver docker-container \
	    --buildkitd-config $(HOME)/.docker/buildkitd.toml --use
	@docker buildx use $(BUILDX_BUILDER)
	@strays=$$(docker buildx ls 2>/dev/null | awk '$$2=="docker-container"{print $$1}' \
	  | grep -v '^\\_' | sed 's/\*$$//' | grep -vxF "$(BUILDX_BUILDER)" | tr '\n' ' '); \
	if [ -n "$$strays" ] && [ -z "$(ALLOW_STRAY_BUILDERS)" ]; then \
	  echo "REFUSING: stray per-project buildx builders are running: $$strays"; \
	  echo "Remove them:  docker buildx rm $$strays"; \
	  exit 1; \
	fi


.PHONY: docker-login-ghcr
docker-login-ghcr: ## Login to GitHub Container Registry
	@echo '$(BLUE)Logging in to GitHub Container Registry...$(NC)'
	@echo "$(GHCR_TOKEN)" | docker login ghcr.io -u $(GITHUB_USER) --password-stdin
	@echo '$(GREEN)GHCR login successful!$(NC)'

# A PUBLIC repo also runs `make release-public` -> GHCR. Visibility decides the image registry
# and nothing else (FLEET-RELEASE-PROCESS §9). This PROMOTES the already-built manifest rather
# than rebuilding: `imagetools create` copies the SAME DIGEST, so the public image is provably
# the artifact that was released — and it needs no registry login of its own.
.PHONY: release-public
release-public: ## [PUBLIC REPO] Scan + promote :$(VERSION) + :latest to GHCR, then publish the GitHub Release
	@if [ -n "$$(git status --porcelain 2>/dev/null)" ]; then \
	  echo "REFUSING: the working tree is dirty, so v$(VERSION) would not describe these bits:"; \
	  git status --short | sed 's/^/    /'; exit 1; \
	fi
	@docker buildx imagetools inspect $(VERSION_IMAGE) >/dev/null 2>&1 \
	  || { echo '$(RED)$(VERSION_IMAGE) not found — run `make release` first$(NC)'; exit 1; }
	@# Scan the exact image being promoted, every platform, BEFORE it reaches the public registry
	@# (repo.release_scans_candidate). `imagetools create` then copies that SAME digest.
	@set -e; ref='$(or $(LUXAUDIT_IMAGE),$(LUXAUDIT))'; \
	if [ -z "$$ref" ]; then echo "REFUSING: set LUXAUDIT_IMAGE to the pinned luxaudit; the image must be scanned before it is pushed"; exit 2; fi; \
	T=$$(mktemp); trap 'rm -f "$$T"' EXIT INT TERM; \
	for plat in $(subst $(comma), ,$(PLATFORM)); do \
	  docker pull -q --platform $$plat $(VERSION_IMAGE) >/dev/null; \
	  docker save $(VERSION_IMAGE) -o "$$T"; chmod 644 "$$T"; \
	  docker run --rm -v $(PWD):/repo -v luxaudit-cache:/root/.cache/trivy -v "$$T":/candidate.tar:ro \
	    "$$ref" --image-archive /candidate.tar --image-label "$(VERSION_IMAGE) ($$plat)"; \
	done; \
	docker pull -q $(VERSION_IMAGE) >/dev/null
	@# ^ Each per-platform pull above leaves THAT platform under the tag locally; the last one is a
	@# foreign arch on this host, and compose then runs it without pulling (exec format error).
	@# Pulling once more with no --platform restores the host's own.
	@# Refuse an already-released public tag: a promotion probes ITS destination (repo.deploy_tag_is_immutable).
	@if docker manifest inspect $(GHCR_IMAGE):$(VERSION) >/dev/null 2>&1; then echo "REFUSING: $(GHCR_IMAGE):$(VERSION) is already released"; exit 1; fi
	$(call refuse_released,$(GHCR_IMAGE):$(VERSION))
	docker buildx imagetools create \
	  -t $(GHCR_IMAGE):$(VERSION) -t $(GHCR_IMAGE):latest \
	  $(VERSION_IMAGE)
	@echo '$(GREEN)Promoted $(VERSION_IMAGE) -> $(GHCR_IMAGE):$(VERSION) + :latest (same digest)$(NC)'
	@$(MAKE) --no-print-directory github-release

.PHONY: docker-pull-cache
docker-pull-cache: ## Pull previous image for cache
	@echo '$(BLUE)Pulling previous image for cache...$(NC)'
	@docker pull $(LOCAL_IMAGE):latest 2>/dev/null || echo "No previous image found for cache (this is normal for first builds)"

.PHONY: docker-build-local
docker-build-local: validate-version validate-structure buildx-setup docker-pull-cache ## Build Docker image (local load, amd64 only for fast iteration)
	@echo '$(BLUE)Building Docker image for local use (amd64 only)...$(NC)'
	DOCKER_BUILDKIT=$(DOCKER_BUILDKIT) docker buildx build \
		--platform $(PLATFORM_DEV) \
		--target production \
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
		-t $(LOCAL_BUILD_IMAGE) \
		--load \
		.
	@echo '$(GREEN)Docker build complete!$(NC)'

.PHONY: github-release
github-release: ## Tag v$(VERSION) and publish the GitHub Release carrying this version's notes
	@# A git tag is NOT a Release: without this the /releases page is empty and
	@# app/release_notes/$(VERSION).md never reaches anyone (repo.github_release_wired,
	@# FLEET-RELEASE-PROCESS §9). Fleet tag convention is v<VERSION> (repo.release_tag_hygiene).
	@test -f app/release_notes/$(VERSION).md || { \
	  echo "missing app/release_notes/$(VERSION).md — write the notes before releasing"; exit 1; }
	@git rev-parse "v$(VERSION)" >/dev/null 2>&1 || git tag -a "v$(VERSION)" -m "$(VERSION)"
	@git push origin "v$(VERSION)"
	@# Idempotent on purpose. A release is re-run after a partial failure far more often than
	@# it is run once cleanly — 2026.09.0 itself had to be re-run after a registry login
	@# aborted the first attempt mid-way. `gh release create` errors with
	@# "Release.tag_name already exists", which would leave a re-run failing at the LAST step
	@# with every image already pushed, i.e. reporting failure on a release that is complete.
	@if gh release view "v$(VERSION)" >/dev/null 2>&1; then \
	  echo "GitHub Release v$(VERSION) exists — updating its notes"; \
	  gh release edit "v$(VERSION)" --title "$(VERSION)" --notes-file "app/release_notes/$(VERSION).md"; \
	else \
	  gh release create "v$(VERSION)" --title "$(VERSION)" --notes-file "app/release_notes/$(VERSION).md"; \
	fi

# v5 shape (luxarch --emit image-block), multi-arch: the build PUSHES a `candidate-<commit>` tag
# nothing pins, each platform of it is pulled + saved + scanned, and only then is :sha-<commit>
# created FROM that digest — so what ships is byte-for-byte what the scan passed
# (repo.release_scans_candidate). A dirty tree is refused: the tag names HEAD, the build reads the
# working directory (repo.deploy_tag_is_immutable).
#
# Split from `release` on purpose: a DEV deploy publishes only the immutable sha. Re-pushing
# :$(VERSION) on every dev build would overwrite the released tag with unreleased code.
# A released version is never re-pushed, not even from its own commit: prod pins it, and a rebuild
# is different bytes under that name (repo.deploy_tag_is_immutable). Fails CLOSED: `manifest
# inspect` exits non-zero for "no such manifest" AND for an unreachable registry, so only the
# registry's own not-found answer reads as unreleased (a DNS blip must not let a re-push through).
define refuse_released
	@out=$$(docker manifest inspect $(1) 2>&1) && { \
	  echo "REFUSING: $(1) is already RELEASED. A released version is immutable: bump VERSION."; exit 1; } || \
	case "$$out" in \
	  *[Nn]"o such manifest"*|*"manifest unknown"*) ;; \
	  *) echo "REFUSING: cannot verify $(1) is unreleased: $$out"; exit 1 ;; \
	esac
endef

.PHONY: publish-sha
publish-sha: validate-version validate-structure buildx-setup docker-pull-cache ## Build, SCAN, then push :sha-<commit> (multi-arch) — the dev-deploy artifact
	@if [ -n "$$(git status --porcelain 2>/dev/null)" ]; then \
	  echo "REFUSING: the working tree is dirty, so sha-$(COMMIT) would not describe these bits:"; \
	  git status --short | sed 's/^/    /'; \
	  echo "Commit first, then publish. make test runs uncommitted code; the stack runs what was pushed."; \
	  exit 1; \
	fi
	DOCKER_BUILDKIT=$(DOCKER_BUILDKIT) docker buildx build \
		--platform $(PLATFORM) \
		--target production \
		--build-arg BUILDKIT_INLINE_CACHE=1 \
		--cache-from $(IMAGE):latest \
		$(BUILD_ARGS) \
		--label "org.opencontainers.image.created=$(CREATED)" \
		--label "org.opencontainers.image.version=$(VERSION)" \
		--label "org.opencontainers.image.title=luxupt" \
		--label "org.opencontainers.image.description=A Docker-based solution for creating time-lapse videos from UniFi Protect cameras" \
		--label "org.opencontainers.image.url=https://github.com/luxardolabs/luxupt" \
		--label "org.opencontainers.image.source=https://github.com/luxardolabs/luxupt" \
		-t $(CANDIDATE_IMAGE) \
		--push \
		.
	@# SCAN THE CANDIDATE, THEN TAG IT. Refuses on any fixable HIGH/CRITICAL in any platform.
	@set -e; ref='$(or $(LUXAUDIT_IMAGE),$(LUXAUDIT))'; \
	if [ -z "$$ref" ]; then echo "REFUSING: set LUXAUDIT_IMAGE to the pinned luxaudit; the candidate must be scanned before it is pushed"; exit 2; fi; \
	T=$$(mktemp); trap 'rm -f "$$T"' EXIT INT TERM; \
	for plat in $(subst $(comma), ,$(PLATFORM)); do \
	  docker pull -q --platform $$plat $(CANDIDATE_IMAGE) >/dev/null; \
	  docker save $(CANDIDATE_IMAGE) -o "$$T"; chmod 644 "$$T"; \
	  docker run --rm -v $(PWD):/repo -v luxaudit-cache:/root/.cache/trivy -v "$$T":/candidate.tar:ro \
	    "$$ref" --image-archive /candidate.tar --image-label "$(CANDIDATE_IMAGE) ($$plat)"; \
	done; \
	docker pull -q $(CANDIDATE_IMAGE) >/dev/null
	@# ^ Each per-platform pull above leaves THAT platform under the tag locally; the last one is a
	@# foreign arch on this host, and compose then runs it without pulling (exec format error).
	@# Pulling once more with no --platform restores the host's own.
	docker buildx imagetools create -t $(SHA_IMAGE) $(CANDIDATE_IMAGE)
	@echo '$(GREEN)Published $(SHA_IMAGE) (candidate scanned clean)$(NC)'

.PHONY: release
release: ## Build, SCAN, then push :sha-<commit> + :$(VERSION) + :latest (multi-arch) to the private registry
	@if docker manifest inspect $(VERSION_IMAGE) >/dev/null 2>&1; then echo "REFUSING: $(VERSION_IMAGE) is already released"; exit 1; fi
	$(call refuse_released,$(VERSION_IMAGE))
	@# The tag is checked on ORIGIN, not only here: a clone without tags would otherwise pass while
	@# origin has v$(VERSION) at another commit. Unreachable origin refuses (fail closed).
	@r=$$(git ls-remote --tags origin "refs/tags/v$(VERSION)^{}" "refs/tags/v$(VERSION)") || { \
	  echo "REFUSING: cannot read tags from origin to verify v$(VERSION) is unreleased"; exit 1; }; \
	t=$$(printf '%s\n' "$$r" | awk '/\^\{\}$$/{print $$1}'); \
	[ -n "$$t" ] || t=$$(printf '%s\n' "$$r" | awk 'NF{print $$1; exit}'); \
	if [ -n "$$t" ] && [ "$$t" != "$$(git rev-parse HEAD)" ]; then \
	  echo "REFUSING: v$(VERSION) is already tagged on origin at $$t, not HEAD: bump VERSION."; exit 1; \
	fi
	@$(MAKE) --no-print-directory publish-sha
	@# :$(VERSION) is created from the sha publish-sha just scanned — same digest, no rebuild.
	docker buildx imagetools create -t $(VERSION_IMAGE) $(SHA_IMAGE)
	@# The alias moves LAST and carries nothing: a label on an already-published artifact.
	docker buildx imagetools create -t $(IMAGE):latest $(SHA_IMAGE)
	@echo ''
	@echo '$(GREEN)Released $(VERSION) to the private registry$(NC)'
	@echo '    $(VERSION_IMAGE)'
	@echo '    $(SHA_IMAGE)'
	@echo ''
	@echo '$(BLUE)Public repo? scan + promote + publish the notes:$(NC)  make release-public'

# ── Point a dev stack at a build (luxarch --emit image-block v5) ────────────────────────────────
# The tag is PERSISTED into .env.<site>-dev, not passed in the deploying shell, because the stack
# has to come back after a reboot: compose reads `${TAG:?}`. Exactly ONE line of that file is
# rewritten in place; nothing else is read, printed or reordered, because it holds secrets.
SITE ?=
DEV_ENV = .env.$(SITE)-dev

define pin_env_tag
	f='$(1)'; t='$(2)'; \
	[ -f "$$f" ] || { echo "$$f is missing — copy .env.example and fill it in first"; exit 1; }; \
	tmp=$$(mktemp); trap 'rm -f "$$tmp"' EXIT; \
	if grep -qE '^[[:space:]]*TAG=' "$$f"; then \
	  awk -v t="$$t" '/^[[:space:]]*TAG=/ && !d {print "TAG=" t; d=1; next} {print}' "$$f" > "$$tmp"; \
	else \
	  cp "$$f" "$$tmp" && printf 'TAG=%s\n' "$$t" >> "$$tmp"; \
	fi; \
	[ -s "$$tmp" ] || { echo "refusing to write an empty $$f"; exit 1; }; \
	o=$$(wc -l < "$$f"); n=$$(wc -l < "$$tmp"); \
	[ "$$n" -ge "$$o" ] || { echo "refusing: rewriting $$f lost lines ($$o -> $$n)"; exit 1; }; \
	cat "$$tmp" > "$$f"; \
	echo "$$f: TAG=$$t"
endef

.PHONY: dev-deploy
dev-deploy: ## Publish THIS commit, pin .env.$(SITE)-dev to it, restart that dev stack, smoke it (SITE=<site>)
	@[ -n "$(SITE)" ] || { echo "usage: make dev-deploy SITE=<site>"; exit 1; }
	@$(MAKE) --no-print-directory publish-sha
	@$(MAKE) --no-print-directory dev-pin SITE=$(SITE) TAG=sha-$(COMMIT)
	@$(MAKE) --no-print-directory smoke SITE=$(SITE)

# The rollback path, and the only one that does not build: name a tag you already published.
# The registry is checked FIRST, so a stack is never pinned to a name the registry never held.
.PHONY: dev-pin
dev-pin: ## Point .env.$(SITE)-dev at an ALREADY-PUBLISHED tag and restart it (rollback path)
	@[ -n "$(SITE)" ] && [ -n "$(TAG)" ] || { echo "usage: make dev-pin SITE=<site> TAG=sha-<commit>|<version>"; exit 1; }
	@docker buildx imagetools inspect $(IMAGE):$(TAG) >/dev/null 2>&1 || \
	  { echo "$(IMAGE):$(TAG) is not in the registry — publish it before pinning a stack to it"; exit 1; }
	@$(call pin_env_tag,$(DEV_ENV),$(TAG))
	@# --wait: return only once the container's healthcheck passes, so the smoke that follows probes
	@# a started app, not one still migrating (a 502 then is the deploy racing itself).
	docker compose --env-file $(DEV_ENV) up -d --wait --wait-timeout 180

# ── Smoke settings (above the emitted block; its own lines are `?=` defaults) ──────────────────
# Each site's dev stack sits behind its own nginx port, so the URL is per SITE. The hosts live in
# the gitignored Makefile.local (SMOKE_URL_<site>, one per SITE): this repo is public.
SMOKE_URL         := $(SMOKE_URL_$(SITE))
SMOKE_ENV_FILE    := $(DEV_ENV)
SMOKE_STATIC_PATH := /static/css/app.css
# /health/live names the commit and answers 200 whatever a dependency does: /health is rightly 503
# while a site's Protect controller is down, and that is not "the stack is not running this build".
SMOKE_HEALTH_PATH := /health/live
# Both entry points: the CLI (python -m app.main) and the uvicorn target entrypoint.sh starts.
SMOKE_ENTRY_MODULES := app.main app.web.main

.PHONY: smoke
# luxarch:smoke asset v3 - DO NOT edit this marker line; it is how repo.emitted_assets_current knows your copy is current. Re-emit with `luxarch --emit smoke`.
# ── Smoke: probe the DEPLOYED stack from outside, after every dev deploy ──────────────────────────
# Emitted by `luxarch --emit smoke`; paste below the image block. `make test` runs the app in-process
# against its test stack, so it cannot see the proxy, the server, the production image or an entry
# point other than the app's. One repo shipped four bugs under a green `make check` that only a probe
# of the deployed stack found: a rate limiter that took the host down, a proxy serving an unstyled UI,
# a standalone script that crashed on a circular import in the production image, and a Host-header
# defence no test exercised. Each probe below is aimed at one of them. `make dev-deploy` runs it
# (`repo.stack_smoke_wired`); a FAIL fails the deploy, and a probe that cannot run says NOT RUN and
# why, never a silent pass.
#
# Settings are `?=` defaults: override them above this block (or in Makefile.local for a host name).

# Setting: where the dev stack is reachable from the build host (e.g. https://dev.example.com) ---
SMOKE_URL ?=
# Setting: the health path; its response must name the build's commit (any field name) -------
SMOKE_HEALTH_PATH ?= /health
# Setting: a static asset the image serves (empty only if the app serves no static files) -----
SMOKE_STATIC_PATH ?= /static/css/app.css
# Setting: a path that needs authentication; the token comes from the SMOKE_AUTH_TOKEN env var -
SMOKE_AUTH_PATH ?=
# Setting: the package directory of standalone entry points, and the image's import root -------
SMOKE_SCRIPTS_DIR ?= app/scripts
SMOKE_IMPORT_ROOT ?= .
# Setting: entry-point MODULES outside that directory. The default is every `*_main` module at the
# app package's root, where the layout standard (fw.module_homes) puts a service's entry points
# (`app.portal_main`); list them yourself for any other place (v3) ---------------------------
SMOKE_ENTRY_MODULES ?= $(basename $(subst /,.,$(patsubst $(SMOKE_IMPORT_ROOT)/%,%,$(wildcard $(SMOKE_IMPORT_ROOT)/app/*_main.py))))
# Setting: the env file the dev stack runs with; each entry point is imported with it, because a
# module that builds the app's settings needs the config the deployed container has (v2) ---------
SMOKE_ENV_FILE ?= .env.dev
# Setting: extra `docker run` options for the entry-point imports (e.g. -e NAME=value) ----------
SMOKE_RUN_OPTS ?=
# Setting: extra curl options (e.g. --cacert <file> for a private CA) -------------------------
SMOKE_CURL_OPTS ?=

smoke: ## Probe the deployed dev stack: build commit, static asset, forged Host refused, auth, standalone entry points
	@set -u; fail=0; \
	[ -n "$(SMOKE_URL)" ] || { echo "REFUSING: set SMOKE_URL to the dev stack's address (Makefile.local)"; exit 2; }; \
	probe() { curl -sS --max-time 15 $(SMOKE_CURL_OPTS) -o "$$B" -w '%{http_code} %{content_type}' "$$@" 2>/dev/null || echo "000 -"; }; \
	B=$$(mktemp); trap 'rm -f "$$B"' EXIT INT TERM; \
	sha=$$(git rev-parse --short=7 HEAD 2>/dev/null || true); \
	r=$$(probe "$(SMOKE_URL)$(SMOKE_HEALTH_PATH)"); \
	if [ -z "$$sha" ]; then echo "FAIL  cannot read this checkout's commit (git rev-parse failed), so the deployed build cannot be checked"; fail=1; \
	elif [ "$${r%% *}" = 200 ] && grep -q "$$sha" "$$B"; then echo "PASS  health names this commit ($$sha)"; \
	else echo "FAIL  $(SMOKE_HEALTH_PATH): $$r, and the response does not name $$sha: the stack is not running this build"; fail=1; fi; \
	if [ -n "$(SMOKE_STATIC_PATH)" ]; then \
	  r=$$(probe "$(SMOKE_URL)$(SMOKE_STATIC_PATH)"); ctype=$${r#* }; \
	  case "$(SMOKE_STATIC_PATH)" in *.css) want=text/css;; *.js|*.mjs) want=javascript;; *) want=;; esac; \
	  if [ "$${r%% *}" = 200 ] && [ -s "$$B" ] && ! grep -qi '<html' "$$B" && { [ -z "$$want" ] || case "$$ctype" in *"$$want"*) true;; *) false;; esac; }; then echo "PASS  static asset served ($(SMOKE_STATIC_PATH), $$ctype)"; \
	  else echo "FAIL  $(SMOKE_STATIC_PATH): $$r: the proxy or image does not serve the built asset as $${want:-a file} (a browser refuses a stylesheet or script with the wrong type)"; fail=1; fi; \
	else echo "NOT RUN  static asset: SMOKE_STATIC_PATH is empty (only right for an app that serves no static files)"; fi; \
	r=$$(probe -H "Host: smoke-forged.invalid" "$(SMOKE_URL)$(SMOKE_HEALTH_PATH)"); \
	case "$${r%% *}" in 2??|3??) echo "FAIL  a forged Host header was answered ($$r): the trusted-host defence is not on in the real stack"; fail=1;; \
	  000) echo "PASS  forged Host refused (connection rejected)";; \
	  *) echo "PASS  forged Host refused ($${r%% *})";; esac; \
	if [ -n "$(SMOKE_AUTH_PATH)" ]; then \
	  r=$$(probe "$(SMOKE_URL)$(SMOKE_AUTH_PATH)"); \
	  case "$${r%% *}" in 401|403) echo "PASS  $(SMOKE_AUTH_PATH) refuses a request with no credentials ($${r%% *})";; \
	    *) echo "FAIL  $(SMOKE_AUTH_PATH) answered a request with NO credentials ($$r): it is not protected"; fail=1;; esac; \
	  if [ -z "$${SMOKE_AUTH_TOKEN:-}" ]; then echo "FAIL  SMOKE_AUTH_PATH is set but SMOKE_AUTH_TOKEN is not in the environment"; fail=1; \
	  else r=$$(probe -H "Authorization: Bearer $${SMOKE_AUTH_TOKEN}" "$(SMOKE_URL)$(SMOKE_AUTH_PATH)"); \
	    if [ "$${r%% *}" = 200 ]; then echo "PASS  authenticated request ($(SMOKE_AUTH_PATH))"; \
	    else echo "FAIL  authenticated $(SMOKE_AUTH_PATH): $$r"; fail=1; fi; fi; \
	else echo "NOT RUN  authenticated request: SMOKE_AUTH_PATH is empty"; fi; \
	n=0; mods=; \
	if [ -d "$(SMOKE_SCRIPTS_DIR)" ]; then for f in $$(find "$(SMOKE_SCRIPTS_DIR)" -name '*.py' ! -name '__init__.py' | sort); do \
	  rel=$$(realpath --relative-to="$(SMOKE_IMPORT_ROOT)" "$$f"); mods="$$mods $$(printf '%s' "$${rel%.py}" | tr / .)"; done; fi; \
	mods=$$(printf '%s\n' $$mods $(SMOKE_ENTRY_MODULES) | sort -u); \
	if [ -n "$$mods" ]; then \
	  envf=; [ -n "$(SMOKE_ENV_FILE)" ] && [ -f "$(SMOKE_ENV_FILE)" ] && envf="--env-file=$(SMOKE_ENV_FILE)"; \
	  if ! docker image inspect "$(SHA_IMAGE)" >/dev/null 2>&1 && ! docker pull -q "$(SHA_IMAGE)" >/dev/null 2>&1; then \
	    echo "FAIL  entry points: $(SHA_IMAGE) is neither built here nor pullable, so no module could be imported (deploy this commit first)"; fail=1; n=-1; \
	  else for mod in $$mods; do \
	    n=$$((n + 1)); \
	    if err=$$(docker run --rm $$envf $(SMOKE_RUN_OPTS) --entrypoint python "$(SHA_IMAGE)" -c "import $$mod" 2>&1 >/dev/null); then echo "PASS  $$mod imports on its own in the production image"; \
	    else fail=1; why=$$(printf '%s\n' "$$err" | grep -E '^[A-Za-z_][A-Za-z0-9_.]*(Error|Exception|Exit)\b' | tail -n 1); why=$${why:-$$(printf '%s\n' "$$err" | grep -v '^[[:space:]]*$$' | tail -n 1)}; \
	      case "$$err" in *ImportError*|*ModuleNotFoundError*|*"circular import"*) echo "FAIL  $$mod does not import on its own in $(SHA_IMAGE) (a circular or missing import the app's own import order hides): $$why";; \
	        *) echo "FAIL  $$mod raised at import in $(SHA_IMAGE): $$why"; [ -n "$$envf" ] || echo "      no env file was passed ($(SMOKE_ENV_FILE) not found): set SMOKE_ENV_FILE to the file the stack runs with";; esac; fi; \
	  done; fi; \
	fi; \
	[ "$$n" -ne 0 ] || echo "NOT RUN  entry points: no module under $(SMOKE_SCRIPTS_DIR) and no SMOKE_ENTRY_MODULES"; \
	exit $$fail

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
		$(LOCAL_BUILD_IMAGE)

.PHONY: shell
shell: ## Open a shell in the Docker container
	@echo '$(BLUE)Opening shell in container...$(NC)'
	docker run --rm -it \
		-v $(PWD)/output:/app/luxupt/output \
		--entrypoint /bin/sh \
		$(LOCAL_BUILD_IMAGE)

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
	@echo ''
	@echo '$(BLUE)Poetry Information:$(NC)'
	@$(POETRY) --version || echo "Poetry not installed"

# =============================================================================
# Production deploy (remote over SSH)
# -----------------------------------------------------------------------------
# repo prod/ is the SOURCE OF TRUTH; prod-sync pushes it, prod-deploy runs it.
# Prod keeps its OWN tiered storage (local NVMe + NAS videos) — NEVER the dev NFS.
# Release flow:  make release && make release-public  ->  (bump prod/compose.yaml default tag)
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
PROD_PORT     ?= 11000
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