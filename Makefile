UV ?= uv
NPM ?= npm
VSCE ?= npx vsce

PY_SOURCES = src tests
BUMP ?= $(firstword $(filter patch minor major,$(MAKECMDGOALS)))
BUMP ?= patch

.PHONY: install lint test check wheel build install-wheel vscode-deps vsix package clean release patch minor major

install:
	$(UV) sync --all-extras
	$(UV) tool install --force --no-binary --no-cache --from . avrae-ls

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

vscode-deps:
	cd vscode-extension && $(NPM) ci

vsix: vscode-deps
	cd vscode-extension && $(NPM) run bundle
	mkdir -p dist
	cd vscode-extension && $(VSCE) package --out ../dist/avrae-ls-client.vsix

package: wheel vsix

release: clean
	$(UV) build
	$(UV) publish

clean:
	rm -rf build dist .ruff_cache .pytest_cache
