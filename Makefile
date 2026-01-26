# ELLE Makefile
# Development, testing, and packaging automation

.PHONY: help install install-dev test lint format typecheck check clean \
        build-deb publish-deb docs serve-docs \
        venv run run-daemon

# Default target
help:
	@echo "ELLE Development Commands"
	@echo ""
	@echo "Setup:"
	@echo "  make install       Install ELLE in current environment"
	@echo "  make install-dev   Install with development dependencies"
	@echo "  make venv          Create virtual environment"
	@echo ""
	@echo "Development:"
	@echo "  make run           Run ELLE CLI"
	@echo "  make run-daemon    Run ELLE daemon"
	@echo "  make test          Run test suite"
	@echo "  make lint          Run linter (ruff)"
	@echo "  make format        Format code (ruff)"
	@echo "  make typecheck     Run type checker (mypy)"
	@echo "  make check         Run all checks (lint, typecheck, test)"
	@echo ""
	@echo "Packaging:"
	@echo "  make build-deb     Build Debian package"
	@echo "  make publish-deb   Publish to APT repository (requires aptly)"
	@echo ""
	@echo "Documentation:"
	@echo "  make docs          Build documentation"
	@echo "  make serve-docs    Serve documentation locally"
	@echo ""
	@echo "Cleanup:"
	@echo "  make clean         Remove build artifacts"

# =============================================================================
# Configuration
# =============================================================================

PYTHON := python3
PIP := pip
VENV := .venv
VENV_BIN := $(VENV)/bin
PACKAGE := elle
VERSION := $(shell $(PYTHON) -c "import tomllib; print(tomllib.load(open('pyproject.toml', 'rb'))['project']['version'])")

# Debian packaging
DEB_NAME := elle_$(VERSION)-1_all.deb
DEB_BUILD_DIR := build/deb
DEB_STAGING := build/deb-staging
APTLY_REPO := elle
APTLY_DIST := jammy
SNAPSHOT_NAME := elle-$(VERSION)

# APT repository settings
APTLY_PUBLIC := /var/aptly/public
APT_REPO_HOST ?= apt.elle.dev
APT_REPO_USER ?= deploy
APT_REPO_PATH ?= /var/www/apt

# =============================================================================
# Setup
# =============================================================================

venv:
	@echo "Creating virtual environment..."
	$(PYTHON) -m venv $(VENV)
	@echo "Activate with: source $(VENV)/bin/activate"

install:
	$(PIP) install -e .

install-dev:
	$(PIP) install -e ".[dev,api,ebpf]"

# =============================================================================
# Development
# =============================================================================

run:
	$(PYTHON) -m elle.cli.oneshot

run-daemon:
	$(PYTHON) -m elle.daemon.main

# =============================================================================
# Testing & Quality
# =============================================================================

test:
	pytest tests/ -v

test-cov:
	pytest tests/ -v --cov=elle --cov-report=term-missing --cov-report=html

lint:
	ruff check src/ tests/

lint-fix:
	ruff check src/ tests/ --fix

format:
	ruff format src/ tests/

format-check:
	ruff format src/ tests/ --check

typecheck:
	mypy src/

# Run all checks
check: lint format-check typecheck test
	@echo "All checks passed!"

# =============================================================================
# Debian Packaging
# =============================================================================

# Build wheel first, then create deb from it
build-wheel:
	@echo "Building Python wheel..."
	$(PYTHON) -m build --wheel
	@echo "Wheel built: dist/elle-$(VERSION)-py3-none-any.whl"

# Build Debian package from wheel (simple dpkg-deb approach)
build-deb: clean-deb build-wheel
	@echo "Building Debian package v$(VERSION)..."

	# Create staging directory structure
	mkdir -p $(DEB_STAGING)/DEBIAN
	mkdir -p $(DEB_STAGING)/usr/lib/python3/dist-packages
	mkdir -p $(DEB_STAGING)/usr/bin
	mkdir -p $(DEB_STAGING)/lib/systemd/system
	mkdir -p $(DEB_STAGING)/usr/share/polkit-1/actions
	mkdir -p $(DEB_STAGING)/usr/share/elle
	mkdir -p $(DEB_STAGING)/usr/share/man/man1
	mkdir -p $(DEB_STAGING)/usr/share/man/man8

	# Extract wheel
	cd $(DEB_STAGING)/usr/lib/python3/dist-packages && unzip -o -q $(CURDIR)/dist/elle-$(VERSION)-py3-none-any.whl

	# Create wrapper scripts
	@echo '#!/usr/bin/python3\nimport sys\nfrom elle.cli.oneshot import main\nif __name__ == "__main__":\n    sys.exit(main())' > $(DEB_STAGING)/usr/bin/elle
	@echo '#!/usr/bin/python3\nimport sys\nfrom elle.daemon.main import main\nif __name__ == "__main__":\n    sys.exit(main())' > $(DEB_STAGING)/usr/bin/elled
	chmod +x $(DEB_STAGING)/usr/bin/elle $(DEB_STAGING)/usr/bin/elled

	# Copy supporting files
	cp packaging/systemd/elled.service $(DEB_STAGING)/lib/systemd/system/
	cp packaging/polkit/com.elle.policy $(DEB_STAGING)/usr/share/polkit-1/actions/
	cp packaging/elle.toml.example $(DEB_STAGING)/usr/share/elle/
	cp packaging/man/elle.1 $(DEB_STAGING)/usr/share/man/man1/
	cp packaging/man/elled.8 $(DEB_STAGING)/usr/share/man/man8/

	# Create DEBIAN control file
	@echo "Package: elle" > $(DEB_STAGING)/DEBIAN/control
	@echo "Version: $(VERSION)-1" >> $(DEB_STAGING)/DEBIAN/control
	@echo "Section: admin" >> $(DEB_STAGING)/DEBIAN/control
	@echo "Priority: optional" >> $(DEB_STAGING)/DEBIAN/control
	@echo "Architecture: all" >> $(DEB_STAGING)/DEBIAN/control
	@echo "Depends: python3 (>= 3.11), python3-pydantic, python3-httpx, python3-ruamel.yaml, python3-rich, python3-prompt-toolkit, python3-toml, libaugeas0, augeas-tools" >> $(DEB_STAGING)/DEBIAN/control
	@echo "Recommends: ollama, python3-fastapi, python3-uvicorn" >> $(DEB_STAGING)/DEBIAN/control
	@echo "Suggests: python3-bcc" >> $(DEB_STAGING)/DEBIAN/control
	@echo "Maintainer: ELLE Contributors <elle@example.com>" >> $(DEB_STAGING)/DEBIAN/control
	@echo "Homepage: https://araujota.github.io/elle" >> $(DEB_STAGING)/DEBIAN/control
	@echo "Description: Enabling Layer Learning Everything" >> $(DEB_STAGING)/DEBIAN/control
	@echo " A local-first, agentic system layer for Ubuntu that converts" >> $(DEB_STAGING)/DEBIAN/control
	@echo " kernel-level telemetry into natural language insight and safe" >> $(DEB_STAGING)/DEBIAN/control
	@echo " system operations." >> $(DEB_STAGING)/DEBIAN/control

	# Copy maintainer scripts
	cp packaging/debian/postinst $(DEB_STAGING)/DEBIAN/ && chmod 755 $(DEB_STAGING)/DEBIAN/postinst
	cp packaging/debian/postrm $(DEB_STAGING)/DEBIAN/ && chmod 755 $(DEB_STAGING)/DEBIAN/postrm
	cp packaging/debian/prerm $(DEB_STAGING)/DEBIAN/ && chmod 755 $(DEB_STAGING)/DEBIAN/prerm

	# Build the deb
	dpkg-deb --build $(DEB_STAGING) build/$(DEB_NAME)

	@echo ""
	@echo "Package built: build/$(DEB_NAME)"
	@echo "To install: sudo dpkg -i build/$(DEB_NAME) && sudo apt-get install -f"

clean-deb:
	rm -rf $(DEB_BUILD_DIR)
	rm -rf $(DEB_STAGING)
	rm -f build/elle_*.deb
	rm -f build/elle_*.buildinfo
	rm -f build/elle_*.changes

# Verify the built package
verify-deb:
	@test -f build/$(DEB_NAME) || (echo "Package not found. Run 'make build-deb' first." && exit 1)
	dpkg-deb -I build/$(DEB_NAME)

# Install the built package locally (for testing)
install-deb:
	@test -f build/$(DEB_NAME) || (echo "Package not found. Run 'make build-deb' first." && exit 1)
	sudo dpkg -i build/$(DEB_NAME)
	sudo apt-get install -f

# =============================================================================
# APT Repository Publishing (aptly snapshots)
# =============================================================================

# Repository setup (one-time):
#   aptly repo create -distribution=jammy -component=main elle
#   # First publish done via: make snapshot-publish
#
# GPG key setup (one-time):
#   gpg --full-generate-key  # Create signing key
#   gpg --export --armor > /var/aptly/public/gpg.key  # Export public key

# Add package to aptly repo
repo-add:
	@test -f build/$(DEB_NAME) || (echo "Package not found. Run 'make build-deb' first." && exit 1)
	@echo "Adding $(DEB_NAME) to aptly repo $(APTLY_REPO)..."
	aptly repo add $(APTLY_REPO) build/$(DEB_NAME)
	@echo "Package added. Create snapshot with: make snapshot-create"

# Create a new snapshot from the repo
snapshot-create:
	@echo "Creating snapshot $(SNAPSHOT_NAME) from repo $(APTLY_REPO)..."
	aptly snapshot create $(SNAPSHOT_NAME) from repo $(APTLY_REPO)
	@echo ""
	@echo "Snapshot $(SNAPSHOT_NAME) created."
	@echo "Publish with: make snapshot-publish"

# Publish a new snapshot (first time)
snapshot-publish:
	@echo "Publishing snapshot $(SNAPSHOT_NAME)..."
	aptly publish snapshot -distribution=$(APTLY_DIST) $(SNAPSHOT_NAME)
	@echo ""
	@echo "Snapshot published to $(APTLY_PUBLIC)"
	@echo ""
	@echo "Add to apt sources:"
	@echo "  deb [signed-by=/path/to/gpg.key] http://your-server/ $(APTLY_DIST) main"

# Switch published repo to a new snapshot (updates)
snapshot-switch:
	@echo "Switching published repo to snapshot $(SNAPSHOT_NAME)..."
	aptly publish switch $(APTLY_DIST) $(SNAPSHOT_NAME)
	@echo ""
	@echo "Repository updated to $(SNAPSHOT_NAME)"

# List all snapshots
snapshot-list:
	aptly snapshot list

# Show snapshot details
snapshot-show:
	aptly snapshot show $(SNAPSHOT_NAME)

# Delete old snapshot (use with caution)
# Usage: make snapshot-delete SNAPSHOT_NAME=elle-0.0.9
snapshot-delete:
	@test -n "$(SNAPSHOT_NAME)" || (echo "Usage: make snapshot-delete SNAPSHOT_NAME=elle-x.y.z" && exit 1)
	aptly snapshot drop $(SNAPSHOT_NAME)

# Full release workflow: build -> add to repo -> snapshot -> publish/switch
release: build-deb repo-add snapshot-create
	@if aptly publish list 2>/dev/null | grep -q "$(APTLY_DIST)"; then \
		echo "Updating existing publication..."; \
		$(MAKE) snapshot-switch; \
	else \
		echo "Creating new publication..."; \
		$(MAKE) snapshot-publish; \
	fi
	@echo ""
	@echo "Release $(VERSION) complete!"
	@echo ""
	@echo "Repository available at: $(APTLY_PUBLIC)"
	@echo "Sync to server with: make upload-apt"

# Upload to remote APT server
upload-apt:
	rsync -avz --delete $(APTLY_PUBLIC)/ $(APT_REPO_USER)@$(APT_REPO_HOST):$(APT_REPO_PATH)/
	@echo "APT repository synced to $(APT_REPO_HOST)"

# Show current repo/publication status
apt-status:
	@echo "=== Aptly Repo ==="
	aptly repo show $(APTLY_REPO)
	@echo ""
	@echo "=== Snapshots ==="
	aptly snapshot list
	@echo ""
	@echo "=== Publications ==="
	aptly publish list

# =============================================================================
# Documentation
# =============================================================================

docs:
	@echo "Documentation is built by GitHub Pages automatically."
	@echo "Push changes to docs/ and they will be deployed."

serve-docs:
	@echo "Serving documentation locally..."
	cd docs && bundle install && bundle exec jekyll serve

# =============================================================================
# Cleanup
# =============================================================================

clean: clean-deb
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info/
	rm -rf src/*.egg-info/
	rm -rf .pytest_cache/
	rm -rf .mypy_cache/
	rm -rf .ruff_cache/
	rm -rf htmlcov/
	rm -rf .coverage
	find . -name "*.pyc" -delete
	find . -name "__pycache__" -type d -delete

# =============================================================================
# Version Management
# =============================================================================

# Display current version
version:
	@echo $(VERSION)

# Bump version (requires manual edit of pyproject.toml and debian/changelog)
# Usage: make bump-version NEW_VERSION=0.2.0
bump-version:
	@test -n "$(NEW_VERSION)" || (echo "Usage: make bump-version NEW_VERSION=x.y.z" && exit 1)
	@echo "Bumping version to $(NEW_VERSION)..."
	@echo ""
	@echo "Manual steps required:"
	@echo "1. Update version in pyproject.toml"
	@echo "2. Add entry to packaging/debian/changelog:"
	@echo ""
	@echo "   elle ($(NEW_VERSION)-1) noble; urgency=medium"
	@echo ""
	@echo "     * Release $(NEW_VERSION)"
	@echo ""
	@echo "    -- ELLE Contributors <elle@example.com>  $$(date -R)"
	@echo ""
	@echo "3. Commit changes: git commit -am 'chore: bump version to $(NEW_VERSION)'"
	@echo "4. Tag release: git tag v$(NEW_VERSION)"
	@echo "5. Push: git push && git push --tags"
