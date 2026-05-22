.PHONY: up down migrate revision run fmt psql build

up:
	docker compose up -d postgres

down:
	docker compose down

build:
	docker compose build app

migrate:
	docker compose run --rm app uv run alembic upgrade head

revision:
	docker compose run --rm app uv run alembic revision --autogenerate -m "$(msg)"

run:
	docker compose run --rm app

fmt:
	uv run black .
	uv run isort .

psql:
	docker compose exec postgres psql -U zizek -d zizek
