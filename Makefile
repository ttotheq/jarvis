.PHONY: help install test lint format type check clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

install: ## Create the venv and install all dev dependencies
	uv sync

test: ## Run the test suite with the coverage gate
	uv run pytest

lint: ## Lint with ruff
	uv run ruff check .

format: ## Auto-format with ruff
	uv run ruff format .

type: ## Static type-check with mypy
	uv run mypy

check: lint type test ## Run the full CI gate locally (lint + type + test)

clean: ## Remove caches and build artifacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage coverage.xml dist build
	find . -type d -name __pycache__ -exec rm -rf {} +
