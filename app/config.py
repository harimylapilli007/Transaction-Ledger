import os
import re

from dotenv import load_dotenv

# Local runs read settings from .env; on Render they come from the dashboard.
load_dotenv()

_SCHEMA_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Point it at a PostgreSQL database "
            "(e.g. Supabase -> Connect -> Session pooler, or a local Postgres)."
        )
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    if not url.startswith("postgresql://"):
        raise RuntimeError("DATABASE_URL must be a PostgreSQL connection string.")
    # Supabase only accepts TLS connections.
    if "supabase" in url and "sslmode=" not in url:
        url += ("&" if "?" in url else "?") + "sslmode=require"
    return url


def database_schema() -> str | None:
    """Optional Postgres schema for all tables. Tests use a throwaway one."""
    schema = os.getenv("DB_SCHEMA") or None
    if schema and not _SCHEMA_NAME.match(schema):
        raise RuntimeError("DB_SCHEMA must be a lowercase Postgres identifier.")
    return schema
