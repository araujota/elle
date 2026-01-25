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
APTLY_REPO := elle-stable

# APT repository settings (for publish-deb)
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

build-deb: clean-deb
	@echo "Building Debian package v$(VERSION)..."

	# Create build directory structure
	mkdir -p $(DEB_BUILD_DIR)

	# Copy source to build directory
	cp -r . $(DEB_BUILD_DIR)/elle-$(VERSION)
	rm -rf $(DEB_BUILD_DIR)/elle-$(VERSION)/.git
	rm -rf $(DEB_BUILD_DIR)/elle-$(VERSION)/.venv
	rm -rf $(DEB_BUILD_DIR)/elle-$(VERSION)/build
	rm -rf $(DEB_BUILD_DIR)/elle-$(VERSION)/__pycache__
	find $(DEB_BUILD_DIR)/elle-$(VERSION) -name "*.pyc" -delete
	find $(DEB_BUILD_DIR)/elle-$(VERSION) -name "__pycache__" -type d -delete

	# Copy debian directory to package root
	cp -r $(DEB_BUILD_DIR)/elle-$(VERSION)/packaging/debian $(DEB_BUILD_DIR)/elle-$(VERSION)/debian

	# Build the package
	cd $(DEB_BUILD_DIR)/elle-$(VERSION) && dpkg-buildpackage -us -uc -b

	# Move package to build/
	mv $(DEB_BUILD_DIR)/$(DEB_NAME) build/

	@echo ""
	@echo "Package built: build/$(DEB_NAME)"
	@echo ""
	@echo "To verify: lintian build/$(DEB_NAME)"
	@echo "To install: sudo dpkg -i build/$(DEB_NAME)"

clean-deb:
	rm -rf $(DEB_BUILD_DIR)
	rm -f build/elle_*.deb
	rm -f build/elle_*.buildinfo
	rm -f build/elle_*.changes

# Verify the built package
verify-deb:
	@test -f build/$(DEB_NAME) || (echo "Package not found. Run 'make build-deb' first." && exit 1)
	lintian build/$(DEB_NAME)

# Install the built package locally (for testing)
install-deb:
	@test -f build/$(DEB_NAME) || (echo "Package not found. Run 'make build-deb' first." && exit 1)
	sudo dpkg -i build/$(DEB_NAME)
	sudo apt-get install -f

# =============================================================================
# APT Repository Publishing
# =============================================================================

# Publish to self-hosted APT repository using aptly
# Requires: aptly installed and configured on build machine
#
# Repository setup (one-time, on build machine):
#   aptly repo create -distribution=stable -component=main elle-stable
#   aptly publish repo elle-stable
#
# GPG key setup (one-time):
#   gpg --full-generate-key  # Create signing key
#   gpg --export --armor > /var/www/apt/gpg.key  # Export public key
#
publish-deb:
	@test -f build/$(DEB_NAME) || (echo "Package not found. Run 'make build-deb' first." && exit 1)
	@echo "Publishing $(DEB_NAME) to $(APTLY_REPO)..."

	# Add package to aptly repo
	aptly repo add $(APTLY_REPO) build/$(DEB_NAME)

	# Update published repository
	aptly publish update stable

	@echo ""
	@echo "Package published!"
	@echo ""
	@echo "Sync to server with:"
	@echo "  rsync -avz ~/.aptly/public/ $(APT_REPO_USER)@$(APT_REPO_HOST):$(APT_REPO_PATH)/"

# Upload to remote APT server
# Assumes aptly publish directory is at ~/.aptly/public/
upload-apt:
	rsync -avz --delete ~/.aptly/public/ $(APT_REPO_USER)@$(APT_REPO_HOST):$(APT_REPO_PATH)/
	@echo "APT repository synced to $(APT_REPO_HOST)"

# Full release: build, verify, publish, upload
release: build-deb verify-deb publish-deb upload-apt
	@echo ""
	@echo "Release complete! Version $(VERSION) is now available."
	@echo ""
	@echo "Users can install with:"
	@echo "  sudo apt update && sudo apt install elle"

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
