.DEFAULT_GOAL := help
SHELL := /bin/bash
.SHELLFLAGS := -euo pipefail -c

COMPOSE := docker compose
BACKEND := cd backend &&
WEB := cd web &&
SIGNING_KEY := .secrets/jwt_signing_key.pem

.PHONY: help
help: ## List the targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

# ----------------------------------------------------------------------------- setup

.env:
	@umask 077; { \
	  echo "POSTGRES_PASSWORD=$$(openssl rand -hex 24)"; \
	  echo "KB_APP_DB_PASSWORD=$$(openssl rand -hex 24)"; \
	  echo "REDIS_PASSWORD=$$(openssl rand -hex 24)"; \
	  echo "S3_ACCESS_KEY=kb-$$(openssl rand -hex 8)"; \
	  echo "S3_SECRET_KEY=$$(openssl rand -hex 24)"; \
	  echo "KB_SEED_PASSWORD=demo-$$(openssl rand -hex 8)"; \
	  echo "KB_UID=$$(id -u)"; \
	  echo "KB_GID=$$(id -g)"; \
	  echo "KB_API_PORT=8000"; \
	  echo "KB_WEB_PORT=3000"; \
	} > .env
	@echo "wrote .env with random secrets"

.PHONY: env
env: .env ## Generate .env with random secrets (never overwrites)

$(SIGNING_KEY):
	@mkdir -p -m 700 .secrets
	$(BACKEND) uv run python -m kb_assistant.cli generate-signing-key --out ../$(SIGNING_KEY)

.PHONY: keys
keys: $(SIGNING_KEY) ## Generate the local JWT signing key (never overwrites)

.PHONY: install
install: ## Install backend and web dependencies, and the git hooks
	$(BACKEND) uv sync --frozen
	$(WEB) npm ci
	uvx pre-commit install

# ----------------------------------------------------------------------------- stack

.PHONY: up
up: env keys ## Build and start the whole stack
	$(COMPOSE) up --build --detach --wait
	@source .env && echo "API  http://127.0.0.1:$${KB_API_PORT:-8000}/docs" \
	  && echo "Web  http://127.0.0.1:$${KB_WEB_PORT:-3000}"

.PHONY: seed
seed: ## Create the demo tenants and users (password: KB_SEED_PASSWORD in .env)
	$(COMPOSE) --profile seed run --rm seed

.PHONY: demo
demo: seed ## Seed, then load the 31 evaluation documents into the demo tenants
	scripts/load-demo-corpus.sh

.PHONY: down
down: ## Stop the stack (data is kept)
	$(COMPOSE) down

.PHONY: clean
clean: ## Stop the stack and delete its data
	$(COMPOSE) down --volumes

.PHONY: logs
logs: ## Follow the logs
	$(COMPOSE) logs --follow

# ----------------------------------------------------------------------------- quality

.PHONY: lint
lint: ## Lint and type-check everything
	$(BACKEND) uv run ruff format --check . && uv run ruff check . && uv run mypy && uv run lint-imports
	$(WEB) npm run lint && npm run typecheck && npm run format:check

.PHONY: test
test: ## Run every test (needs Docker for testcontainers)
	$(BACKEND) uv run pytest --cov --cov-report=term
	$(WEB) npm test
	$(BACKEND) uv run coverage report --include="src/kb_assistant/domain/*,src/kb_assistant/application/*" --fail-under=80

.PHONY: eval
eval: ## Run the evaluation and its quality gate; reports in eval/reports (KB_EVAL_PROFILE=openai for hosted models)
	$(BACKEND) KB_EVAL_REPORT_DIR=../eval/reports uv run pytest -m eval -q

.PHONY: check
check: lint test eval ## What CI runs
