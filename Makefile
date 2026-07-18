.DEFAULT_GOAL := help
.PHONY: help up down logs migrate revision test fmt shell

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-12s %s\n", $$1, $$2}'

up: ## Start db, redis, api and apply migrations
	docker compose up --build

down: ## Stop and remove containers
	docker compose down

logs: ## Tail api logs
	docker compose logs -f api

migrate: ## Apply migrations
	docker compose exec api alembic upgrade head

revision: ## Autogenerate a migration:  make revision m="add risks"
	docker compose exec api alembic revision --autogenerate -m "$(m)"

test: ## Run tests in the api container
	docker compose exec api pytest -q

fmt: ## Lint and format
	docker compose exec api ruff check --fix . && docker compose exec api ruff format .

shell: ## Shell into the api container
	docker compose exec api bash
