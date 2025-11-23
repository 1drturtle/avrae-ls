UV ?= uv
NPM ?= npm
VSCE ?= npx vsce

PY_SOURCES = src tests

.PHONY: install lint test check wheel build install-wheel vscode-deps vsix package clean release

install:
	$(UV) sync --all-extras

lint:
	$(UV) run ruff check $(PY_SOURCES)

test:
	$(UV) run pytest tests --cov=src

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
