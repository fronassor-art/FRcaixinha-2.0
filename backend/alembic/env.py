from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context
from app.db.base import Base
from app.core.config import settings
import app.models
from app.db.alembic_version_table import prepare_version_table

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)
if config.config_file_name and config.get_section("loggers"):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

def run_migrations_offline():
    raise RuntimeError(
        "Offline Alembic migrations are disabled: the installed Alembic "
        "does not expose a public version_num length override, and emitting "
        "offline SQL with VARCHAR(32) would diverge from the online flow."
    )

def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            prepare_version_table(connection)
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
