"""Alembic environment: tells Alembic which database to use and which tables exist."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

# Importing the models package registers every table on Base.metadata.
import drivetruagent.db.models  # noqa: F401
from drivetruagent.config import get_settings
from drivetruagent.db.base import Base

config = context.config

if config.config_file_name is not None:
    # Keep loggers created before this point (pytest's, for example) working.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def get_database_url() -> str:
    """Use a URL passed in through the config (tests do this), else the app settings."""
    return config.get_main_option("sqlalchemy.url") or get_settings().database_url


def run_migrations_offline() -> None:
    """Write the migration SQL to stdout without connecting (alembic upgrade --sql)."""
    context.configure(
        url=get_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Connect to the database and run the migrations."""
    engine = create_engine(get_database_url(), poolclass=pool.NullPool)

    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()

    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
