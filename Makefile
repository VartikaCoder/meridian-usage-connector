# One entry point for humans and CI. Requires: uv (Python), Node >= 20.
ENV ?= dev
WHO ?= priya.nair@meridianls.example
PORT_dev := 8000
PORT_staging := 8001
PORT_prod := 8002
PROVISION := cd service && uv run python -m meridian_usage.provisioning

.PHONY: setup test lint typecheck check serve provision provision-check build bundle package \
	install-desktop install-desktop-preview demo

setup:
	cd service && uv sync
	cd connector && npm install

test:
	cd service && uv run pytest
	cd connector && npm test

lint:
	cd service && uv run ruff check . && uv run ruff format --check .

typecheck:
	cd service && uv run mypy src
	cd connector && npm run typecheck

check: lint typecheck test provision-check

# Render permissions manifests and host registration for dev, staging and prod, and validate.
provision:
	$(PROVISION) render

# Validate, and fail if provisioning/rendered is stale or hand-edited (run in CI).
provision-check:
	$(PROVISION) check

build:
	cd connector && npm run build

# The connector as one self-contained file (what the plugin and Claude Desktop run).
bundle:
	cd connector && npm run bundle

# A local plugin marketplace in dist/marketplace: one plugin per environment.
package: bundle
	$(PROVISION) package

# Register the connector in Claude Desktop as $(WHO) against the $(ENV) service.
install-desktop-preview: bundle
	$(PROVISION) desktop --env $(ENV) --as $(WHO)

install-desktop: bundle
	$(PROVISION) desktop --env $(ENV) --as $(WHO) --write

# Transcript of the three demo cases over stdio MCP; needs `make serve ENV=prod` running.
demo: bundle
	cd connector && npm run --silent demo > ../docs/demo-transcript.md
	@echo "Wrote docs/demo-transcript.md"

# Admin page and API: dev on :8000, staging on :8001, prod on :8002.
serve:
	cd service && MERIDIAN_ENV=$(ENV) uv run uvicorn --factory meridian_usage.app:create_app --reload --port $(PORT_$(ENV))
