.PHONY: up down build migrate revision run ingest start nuke fmt psql

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

# Launch the Textual UI inside the app container — interactive (TTY attached
# via compose's tty/stdin_open). Postgres is auto-started as a dependency, but
# you must have run `make migrate` at least once for the schema to exist.
run:
	docker compose run --rm app

# Headless ingest of one EPUB via the CLI entrypoint.
# Usage: make ingest path=Absolute-Recoil.epub
# `path` is relative to ./books on the host (mounted at /app/books in the container).
ingest:
	docker compose run --rm app uv run python -m src.entrypoints.cli ingest /app/books/$(path)

fmt:
	uv run black .
	uv run isort .

psql:
	docker compose exec postgres psql -U zizek -d zizek
