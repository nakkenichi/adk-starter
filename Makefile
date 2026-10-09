# Developer entry points. `make check` runs exactly what the CI workflow runs on a pull request.
SHELL := /bin/bash
.DEFAULT_GOAL := help

AGENTS_CLI := uvx google-agents-cli==1.5.0
PYPI_INDEX := https://pypi.org/simple
SHELLCHECK := uvx --from shellcheck-py==0.11.0.1 shellcheck
ACTIONLINT := uvx --from actionlint-py==1.7.12.25 --with shellcheck-py==0.11.0.1 actionlint

.PHONY: help init install lock lint format typecheck test check playground run serve eval \
	tf-fmt tf-validate bootstrap deploy

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

init: ## Turn the template into your agent: make init NAME=acme-invoice-agent [DISPLAY_NAME="ACME Invoice Agent"]
	@test -n "$(NAME)" || { echo "usage: make init NAME=<kebab-case-name> [DISPLAY_NAME='<Display Name>']"; exit 2; }
	uv run --no-project --python 3.12 python scripts/init_project.py --name "$(NAME)" \
		$(if $(DISPLAY_NAME),--display-name "$(DISPLAY_NAME)")

install: ## Install dependencies from uv.lock (including dev tools)
	uv sync --frozen

lock: ## Re-resolve uv.lock against public PyPI
	uv lock --no-config --default-index $(PYPI_INDEX)

lint: ## Ruff lint + format check, ShellCheck and actionlint
	uv run ruff check .
	uv run ruff format --check .
	$(SHELLCHECK) scripts/*.sh
	$(ACTIONLINT)

format: ## Auto-fix lint findings and format the code
	uv run ruff check --fix .
	uv run ruff format .

typecheck: ## mypy --strict over app, tests and scripts
	uv run mypy app tests scripts --strict

test: ## Offline unit tests with the coverage gate
	uv run pytest --cov --cov-report=term-missing

check: lint typecheck test ## Everything CI runs on a pull request

playground: ## Local ADK web playground (reads .env; uses Application Default Credentials)
	$(AGENTS_CLI) playground

run: ## One-shot prompt against the local agent: make run PROMPT="What can you do?"
	@test -n "$(PROMPT)" || { echo 'usage: make run PROMPT="..."'; exit 2; }
	$(AGENTS_CLI) run "$(PROMPT)"

serve: ## Serve the production FastAPI app (ADK API, A2A, Agent Runtime routes) on :8080
	uv run uvicorn app.fast_api_app:app --host 127.0.0.1 --port 8080 --reload

eval: ## Live evaluation: local agent + Vertex AI grading (see tests/eval/README.md)
	$(AGENTS_CLI) eval run --qps 0.5

tf-fmt: ## Format Terraform files
	terraform -chdir=terraform fmt -recursive

tf-validate: ## Validate Terraform without touching remote state
	terraform -chdir=terraform init -backend=false -input=false
	terraform -chdir=terraform validate

bootstrap: ## Provision GCP and wire GitHub: make bootstrap PROJECT_ID=my-project PREFIX=acme [ARGS="--deployment-target cloud_run"]
	@test -n "$(PROJECT_ID)" && test -n "$(PREFIX)" || { echo "usage: make bootstrap PROJECT_ID=<project> PREFIX=<3-16 chars> [ARGS=...]"; exit 2; }
	./scripts/bootstrap_gcp.sh --project-id "$(PROJECT_ID)" --prefix "$(PREFIX)" $(ARGS)

deploy: ## Deploy from your machine (reads GCP_* env vars; see ./scripts/deploy.sh --help)
	./scripts/deploy.sh $(ARGS)
