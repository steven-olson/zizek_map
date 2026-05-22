#!/usr/bin/env python3
import argparse
import subprocess
import sys
from pathlib import Path


class IngestSmokeTest:
    """End-to-end smoke runner for the Absolute Recoil ingest pipeline.

    Orchestrates a docker-compose flow that's deliberately fast and self-describing:
    bring backing services up, optionally rebuild the app image and apply
    migrations, optionally wipe the prior book row, then invoke the CLI ingest
    inside the app container while its JSON-formatted stdout streams straight
    to this process's stdout. On a non-zero ingest exit the script dumps the
    tail of the postgres container log so an agent reading the transcript sees
    DB-side errors (FK violations etc.) without a second command.
    """

    # Curly apostrophe — match the actual filename byte-for-byte.
    _BOOK_FILENAME = (
        "Absolute Recoil -- Slavoj Zizek -- Lightning Source Inc_ -- "
        "4a9843c44f84fd2743cc65310acd14e7 -- Anna’s Archive.epub"
    )
    _CONTAINER_BOOK_PATH = f"/app/books/{_BOOK_FILENAME}"
    _POSTGRES_TAIL_LINES = 50

    def __init__(self, repo_root: Path) -> None:
        """Pin the working directory we'll invoke docker-compose from.

        Intent: every subprocess runs with `cwd=repo_root` so the script is
        directory-independent — `./local_scripts/smoke_ingest.py` and
        `python /abs/path/local_scripts/smoke_ingest.py` behave identically.
        """
        self._repo_root = repo_root

    def execute(self, *, build: bool, migrate: bool, reset: bool) -> int:
        """Run the smoke sequence and return the ingest CLI's exit code.

        Intent: the caller (main) does nothing but parse args and forward this
        return value to sys.exit, so the script's exit status mirrors the inner
        CLI's outcome verbatim.
        """
        if build:
            self._step("build app image", ["docker", "compose", "build", "app"])
        self._step(
            "start backing services",
            ["docker", "compose", "up", "-d", "postgres", "adminer", "dozzle"],
        )
        if migrate:
            self._step(
                "apply migrations",
                [
                    "docker",
                    "compose",
                    "run",
                    "--rm",
                    "app",
                    "uv",
                    "run",
                    "alembic",
                    "upgrade",
                    "head",
                ],
            )
        if reset:
            self._reset_book_row()

        exit_code = self._step(
            "run ingest (streaming app stdout)",
            [
                "docker",
                "compose",
                "run",
                "--rm",
                "app",
                "uv",
                "run",
                "python",
                "-m",
                "src.entrypoints.cli",
                "ingest",
                self._CONTAINER_BOOK_PATH,
            ],
            check=False,
        )

        if exit_code != 0:
            self._dump_postgres_tail()
            print(f"\nSMOKE FAILED: ingest exited with code {exit_code}", file=sys.stderr)
        else:
            print("\nSMOKE OK: ingest completed cleanly")
        return exit_code

    def _step(self, label: str, cmd: list[str], check: bool = True) -> int:
        """Print a clear banner, run `cmd` with stdout/stderr passthrough, return code.

        Intent: each step is announced on its own banner line so the failure
        point in a long transcript is obvious to both humans and agents. When
        `check=True`, any non-zero exit aborts the script — used for setup
        steps where continuing would only mask the root cause.
        """
        print(f"\n=== {label} ===", flush=True)
        print(f"$ {' '.join(cmd)}", flush=True)
        result = subprocess.run(cmd, cwd=self._repo_root)
        if check and result.returncode != 0:
            print(
                f"FATAL: '{label}' failed with exit code {result.returncode}",
                file=sys.stderr,
            )
            sys.exit(result.returncode)
        return result.returncode

    def _reset_book_row(self) -> None:
        """DELETE the existing Book row for our fixture, letting CASCADE wipe descendants.

        Intent: the LLM response cache (keyed on file hash) stays intact, so the
        next ingest is still fast — but the pipeline's persist + idempotency
        paths are exercised end-to-end instead of short-circuiting on
        SkippedAlreadyIngested.
        """
        escaped = self._CONTAINER_BOOK_PATH.replace("'", "''")
        sql = f"DELETE FROM books WHERE file_path = '{escaped}';"
        self._step(
            "reset prior book row",
            [
                "docker",
                "compose",
                "exec",
                "-T",
                "postgres",
                "psql",
                "-U",
                "zizek",
                "-d",
                "zizek",
                "-c",
                sql,
            ],
        )

    def _dump_postgres_tail(self) -> None:
        """Print the last N lines of the postgres container's log to stderr.

        Intent: the app container's stdout already streamed inline — the missing
        side on a failure is the DB log (e.g. FK violation detail, deadlocks).
        Surfacing it automatically here means an agent sees the smoking gun in
        one transcript without having to remember to run `docker compose logs`.
        """
        print(
            f"\n=== last {self._POSTGRES_TAIL_LINES} lines of postgres logs (failure dump) ===",
            file=sys.stderr,
            flush=True,
        )
        subprocess.run(
            ["docker", "compose", "logs", f"--tail={self._POSTGRES_TAIL_LINES}", "postgres"],
            cwd=self._repo_root,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Smoke-test the ingest pipeline end-to-end via docker compose. "
            "Runs the Absolute Recoil EPUB through the full pipeline, streaming "
            "the app's JSON logs and dumping postgres logs on failure."
        ),
    )
    parser.add_argument(
        "--no-build",
        action="store_true",
        help="Skip `docker compose build app` (assume the image is current).",
    )
    parser.add_argument(
        "--skip-migrate",
        action="store_true",
        help="Skip `alembic upgrade head` (assume the schema is current).",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help=(
            "DELETE any prior Book row for this file_path before ingesting, so "
            "the pipeline runs fresh instead of short-circuiting on the "
            "idempotency check. Leaves the LLM response cache intact."
        ),
    )
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parent.parent
    smoke = IngestSmokeTest(repo_root=repo_root)
    return smoke.execute(
        build=not args.no_build,
        migrate=not args.skip_migrate,
        reset=args.reset,
    )


if __name__ == "__main__":
    sys.exit(main())
