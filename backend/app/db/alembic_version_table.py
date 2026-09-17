"""Prepare Alembic's version table without changing migration identifiers."""

from sqlalchemy import Column, MetaData, PrimaryKeyConstraint, String, Table, inspect
from sqlalchemy.engine import Connection


VERSION_NUM_CAPACITY = 64


def _qualified_identifier(connection: Connection, table: str, schema: str | None) -> str:
    preparer = connection.dialect.identifier_preparer
    qualified_table = preparer.quote(table)
    if schema:
        return f"{preparer.quote_schema(schema)}.{qualified_table}"
    return qualified_table


def _version_table(
    *,
    version_table: str,
    version_table_schema: str | None,
    version_table_pk: bool,
) -> Table:
    table = Table(
        version_table,
        MetaData(),
        Column("version_num", String(VERSION_NUM_CAPACITY), nullable=False),
        schema=version_table_schema,
    )
    if version_table_pk:
        table.append_constraint(
            PrimaryKeyConstraint("version_num", name=f"{version_table}_pkc")
        )
    return table


def prepare_version_table(
    connection: Connection,
    *,
    version_table: str = "alembic_version",
    version_table_schema: str | None = None,
    version_table_pk: bool = True,
) -> None:
    """Create or widen Alembic's version table before migrations run."""
    inspector = inspect(connection)
    if not inspector.has_table(version_table, schema=version_table_schema):
        _version_table(
            version_table=version_table,
            version_table_schema=version_table_schema,
            version_table_pk=version_table_pk,
        ).create(connection, checkfirst=False)
        return

    if connection.dialect.name == "sqlite":
        return

    columns = inspector.get_columns(version_table, schema=version_table_schema)
    version_column = next(
        (column for column in columns if column["name"] == "version_num"),
        None,
    )
    if version_column is None:
        raise RuntimeError(
            f"{version_table!r} exists without the required version_num column"
        )

    declared_length = getattr(version_column["type"], "length", None)
    if declared_length is None or declared_length >= VERSION_NUM_CAPACITY:
        return

    if connection.dialect.name != "postgresql":
        raise RuntimeError(
            "Cannot safely widen an existing Alembic version table for "
            f"dialect {connection.dialect.name!r}"
        )

    qualified_table = _qualified_identifier(
        connection, version_table, version_table_schema
    )
    quoted_column = connection.dialect.identifier_preparer.quote("version_num")
    connection.exec_driver_sql(
        f"ALTER TABLE {qualified_table} "
        f"ALTER COLUMN {quoted_column} TYPE VARCHAR({VERSION_NUM_CAPACITY})"
    )
