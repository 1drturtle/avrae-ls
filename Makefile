UV ?= uv
NPM ?= npm
VSCE ?= npx vsce

PY_SOURCES = src tests

.PHONY: install lint test check wheel build install-wheel vscode-deps vsix package clean

install:
	$(UV) sync --all-extras

lint:
	$(UV) run ruff check $(PY_SOURCES)

test:
	$(UV) run pytest tests

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

clean:
	rm -rf build dist .ruff_cache .pytest_cache
