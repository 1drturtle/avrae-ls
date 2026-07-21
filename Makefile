UV ?= uv
NPM ?= npm
VSCE ?= npx vsce

PY_SOURCES = src tests
BUNDLE_DIR = vscode-extension/bundled
BUNDLE_SITE_PACKAGES = $(BUNDLE_DIR)/site-packages
BUNDLE_REQUIREMENTS = $(BUNDLE_DIR)/requirements.txt
PROJECT_VERSION := $(shell sed -n 's/^version = "\([^"]*\)"/\1/p' pyproject.toml)
BUNDLE_WHEEL = dist/avrae_ls-$(PROJECT_VERSION)-py3-none-any.whl
BUMP ?= $(firstword $(filter patch minor major,$(MAKECMDGOALS)))
BUMP ?= patch

.PHONY: install lint test check wheel build install-wheel bundle-vscode-server verify-vscode-bundle vscode-deps vsix package clean release patch minor major

install:
	$(UV) sync --all-extras
	$(UV) tool install --force --no-cache --from . avrae-ls

lint:
	$(UV) run ruff check $(PY_SOURCES)

test:
	$(UV) run pytest tests --cov=src

bump-version:
	$(UV) run scripts/bump_version.py $(BUMP)
	$(UV) lock

# swallow positional bump targets so `make bump-version minor` works
patch minor major:
	@:

check: lint test

wheel:
	$(UV) build

build: wheel

install-wheel: wheel
	$(UV) pip install --force-reinstall dist/avrae_ls-*.whl

bundle-vscode-server: wheel
	rm -rf $(BUNDLE_DIR)
	mkdir -p $(BUNDLE_SITE_PACKAGES)
	$(UV) export --frozen --no-dev --no-emit-project --format requirements.txt --output-file $(BUNDLE_REQUIREMENTS)
	$(UV) pip install --target $(BUNDLE_SITE_PACKAGES) --require-hashes -r $(BUNDLE_REQUIREMENTS)
	$(UV) pip install --target $(BUNDLE_SITE_PACKAGES) --no-deps $(BUNDLE_WHEEL)
	rm $(BUNDLE_REQUIREMENTS)

vscode-deps:
	cd vscode-extension && $(NPM) ci

vsix: bundle-vscode-server vscode-deps
	cd vscode-extension && $(NPM) run bundle
	mkdir -p dist
	cd vscode-extension && $(VSCE) package --out ../dist/avrae-ls-client.vsix

verify-vscode-bundle:
	test -f $(BUNDLE_SITE_PACKAGES)/avrae_ls/__main__.py
	test -f $(BUNDLE_SITE_PACKAGES)/draconic/__init__.py
	test -d $(BUNDLE_SITE_PACKAGES)/d20
	test -d $(BUNDLE_SITE_PACKAGES)/httpx
	test -d $(BUNDLE_SITE_PACKAGES)/lsprotocol
	test -d $(BUNDLE_SITE_PACKAGES)/pygls
	test -d $(BUNDLE_SITE_PACKAGES)/yaml
	PYTHONPATH="$(BUNDLE_SITE_PACKAGES):$$PYTHONPATH" $(UV) run --no-sync python -m avrae_ls --help
	unzip -Z1 dist/avrae-ls-client.vsix | rg -qx 'extension/bundled/site-packages/avrae_ls/__main__.py'
	unzip -Z1 dist/avrae-ls-client.vsix | rg -qx 'extension/bundled/site-packages/draconic/__init__.py'
	unzip -Z1 dist/avrae-ls-client.vsix | rg -qx 'extension/bundled/site-packages/pygls/__init__.py'

package: wheel vsix

release: clean
	$(UV) build
	$(UV) publish

clean:
	rm -rf build dist .ruff_cache .pytest_cache $(BUNDLE_DIR)
