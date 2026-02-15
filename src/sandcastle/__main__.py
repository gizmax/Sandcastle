"""CLI entrypoint for `python -m sandcastle`."""

from __future__ import annotations

import sys


def main() -> None:
    """Route CLI commands."""
    args = sys.argv[1:]

    if not args or args[0] == "serve":
        import uvicorn

        uvicorn.run(
            "sandcastle.main:app",
            host="0.0.0.0",
            port=8080,
            reload=True,
        )
    elif args[0] == "db" and len(args) > 1 and args[1] == "migrate":
        _run_migrations()
    else:
        print(f"Unknown command: {' '.join(args)}")
        print("Usage:")
        print("  python -m sandcastle serve      - Start the API server")
        print("  python -m sandcastle db migrate  - Run database migrations")
        sys.exit(1)


def _run_migrations() -> None:
    """Run Alembic migrations."""
    try:
        from alembic import command
        from alembic.config import Config

        alembic_cfg = Config("alembic.ini")
        command.upgrade(alembic_cfg, "head")
        print("Migrations applied successfully.")
    except Exception as e:
        print(f"Migration failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
