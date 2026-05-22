.PHONY: up down migrate revision run fmt psql build ingest-all start nuke

up:
	docker compose up -d postgres adminer dozzle

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

ingest-all:
	docker compose run --rm app uv run python local_scripts/ingest_absolute_recoil.py
	docker compose run --rm app uv run python local_scripts/ingest_less_than_nothing.py
	docker compose run --rm app uv run python local_scripts/ingest_sublime_object.py

start: build up migrate run

nuke:
	docker compose down --volumes --rmi all --remove-orphans
