"""A3.77A cycle foundation with additive legacy isolation."""

import re
from alembic import op
import sqlalchemy as sa

revision = "0096_cycle_foundation_a377a"
down_revision = "0095_pix_attempt_provider_reservation"
branch_labels = None
depends_on = None

CYCLE_START = "2026-12-10"
ENTRY_DEADLINE = "2027-01-10"
CLOSING_REFERENCE = "2027-12-10"


def _sqlite_split_definitions(create_sql):
    open_index = create_sql.find("(")
    close_index = create_sql.rfind(")")
    if open_index < 0 or close_index <= open_index:
        raise RuntimeError("Invalid SQLite CREATE TABLE statement")

    body = create_sql[open_index + 1:close_index]
    definitions = []
    start = 0
    depth = 0
    quote = None
    bracket_quote = False

    for index, char in enumerate(body):
        if bracket_quote:
            if char == "]":
                bracket_quote = False
            continue

        if quote is not None:
            if char == quote:
                quote = None
            continue

        if char in ("'", '"', "`"):
            quote = char
            continue
        if char == "[":
            bracket_quote = True
            continue
        if char == "(":
            depth += 1
            continue
        if char == ")":
            depth -= 1
            if depth < 0:
                raise RuntimeError("Unbalanced SQLite CREATE TABLE statement")
            continue
        if char == "," and depth == 0:
            definitions.append(body[start:index].strip())
            start = index + 1

    definitions.append(body[start:].strip())

    if depth != 0 or quote is not None or bracket_quote:
        raise RuntimeError("Unbalanced SQLite CREATE TABLE statement")
    if any(not item for item in definitions):
        raise RuntimeError("Empty definition in SQLite CREATE TABLE statement")

    return open_index, close_index, definitions


def _sqlite_is_table_constraint(definition):
    upper = definition.lstrip().upper()
    return upper.startswith((
        "CONSTRAINT ",
        "PRIMARY KEY",
        "FOREIGN KEY",
        "UNIQUE",
        "CHECK",
    ))


def _sqlite_normalize_columns(value):
    return tuple(
        part.strip().strip('"`[]').lower()
        for part in value.split(",")
    )


def _sqlite_unique_columns(definition):
    match = re.match(
        r"^(?:CONSTRAINT\s+\S+\s+)?UNIQUE\s*\((.*)\)\s*$",
        definition.strip(),
        flags=re.I | re.S,
    )
    if not match:
        return None
    return _sqlite_normalize_columns(match.group(1))


def _sqlite_column_name(definition):
    if _sqlite_is_table_constraint(definition):
        return None
    match = re.match(
        r'^\s*(?:"([^"]+)"|`([^`]+)`|\[([^\]]+)\]|([^\s]+))',
        definition,
    )
    if not match:
        raise RuntimeError("Cannot parse SQLite column definition: " + definition)
    return next(value for value in match.groups() if value is not None).lower()


def _sqlite_schema_objects(bind, table):
    return [
        row[0]
        for row in bind.execute(
            sa.text(
                "SELECT sql FROM sqlite_master "
                "WHERE tbl_name=:name "
                "AND type IN ('index', 'trigger') "
                "AND sql IS NOT NULL "
                "ORDER BY type, name"
            ),
            {"name": table},
        ).fetchall()
    ]


def _sqlite_build_table_sql(original_sql, table, replacement, definitions):
    open_index, close_index, _ = _sqlite_split_definitions(original_sql)

    original_prefix = original_sql[:open_index]

    escaped = re.escape(table)
    table_identifier = (
        r'(?:"' + escaped + r'"'
        r'|`' + escaped + r'`'
        r'|\[' + escaped + r'\]'
        r'|' + escaped + r')'
    )

    pattern = re.compile(
        r'^(\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?)'
        + table_identifier
        + r'(\s*)$',
        flags=re.I | re.S,
    )

    match = pattern.match(original_prefix)
    if not match:
        raise RuntimeError(
            "Cannot rewrite SQLite table name: "
            + table
            + "; prefix="
            + repr(original_prefix)
        )

    prefix = match.group(1) + replacement + match.group(2)
    suffix = original_sql[close_index + 1:]

    return prefix + "(" + ", ".join(definitions) + ")" + suffix


def _sqlite_rebuild(table, new_column, old_unique):
    bind = op.get_bind()
    row = bind.execute(
        sa.text(
            "SELECT sql FROM sqlite_master "
            "WHERE type='table' AND name=:name"
        ),
        {"name": table},
    ).scalar_one()

    _, _, definitions = _sqlite_split_definitions(row)

    columns = []
    constraints = []
    for definition in definitions:
        if _sqlite_is_table_constraint(definition):
            constraints.append(definition)
        else:
            columns.append(definition)

    target_unique = _sqlite_normalize_columns(old_unique)
    kept_constraints = []
    removed_unique = 0

    for constraint in constraints:
        if _sqlite_unique_columns(constraint) == target_unique:
            removed_unique += 1
        else:
            kept_constraints.append(constraint)

    if removed_unique != 1:
        raise RuntimeError(
            f"Expected exactly one UNIQUE({old_unique}) on {table}; "
            f"found {removed_unique}"
        )

    new_name = _sqlite_column_name(new_column)
    if any(_sqlite_column_name(column) == new_name for column in columns):
        raise RuntimeError(f"Column {new_name} already exists on {table}")

    replacement = table + "__a377a"
    schema_objects = _sqlite_schema_objects(bind, table)

    sql = _sqlite_build_table_sql(
        row,
        table,
        replacement,
        columns + [new_column] + kept_constraints,
    )
    op.execute(sa.text(sql))

    copy_columns = [
        item[1]
        for item in bind.execute(
            sa.text("PRAGMA table_info(" + table + ")")
        ).fetchall()
    ]
    op.execute(
        sa.text(
            "INSERT INTO " + replacement
            + " (" + ", ".join(copy_columns) + ") "
            + "SELECT " + ", ".join(copy_columns)
            + " FROM " + table
        )
    )
    op.execute(sa.text("DROP TABLE " + table))
    op.rename_table(replacement, table)

    for schema_sql in schema_objects:
        op.execute(sa.text(schema_sql))


def upgrade():
    op.create_table(
        "cycles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("entry_deadline", sa.Date(), nullable=False),
        sa.Column("closing_reference_date", sa.Date(), nullable=False),
        sa.Column("monthly_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("months", sa.Integer(), nullable=False),
        sa.Column("max_quotas", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="OPEN"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("start_date", name="uq_cycles_start_date"),
        sa.CheckConstraint("entry_deadline >= start_date", name="ck_cycles_entry_deadline_after_start"),
        sa.CheckConstraint("closing_reference_date >= start_date", name="ck_cycles_closing_after_start"),
        sa.CheckConstraint("monthly_amount > 0 AND months >= 1 AND max_quotas >= 1", name="ck_cycles_positive_terms"),
    )
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        _sqlite_rebuild("quotas", "cycle_id INTEGER REFERENCES cycles(id)", "member_id")
        _sqlite_rebuild("contributions", "cycle_id INTEGER REFERENCES cycles(id)", "member_id, competence")
    else:
        op.add_column("quotas", sa.Column("cycle_id", sa.Integer(), sa.ForeignKey("cycles.id"), nullable=True))
        op.add_column("contributions", sa.Column("cycle_id", sa.Integer(), sa.ForeignKey("cycles.id"), nullable=True))
        op.drop_constraint("quotas_member_id_key", "quotas", type_="unique")
        op.drop_constraint("uq_contribution_member_competence", "contributions", type_="unique")
    op.create_index("ix_quotas_cycle_id", "quotas", ["cycle_id"])
    op.create_index("ix_contributions_cycle_id", "contributions", ["cycle_id"])
    op.create_index(
        "ix_quotas_member_cycle",
        "quotas",
        ["member_id", "cycle_id"],
        unique=False,
        postgresql_where=sa.text("cycle_id IS NOT NULL"),
        sqlite_where=sa.text("cycle_id IS NOT NULL"),
    )
    op.create_index(
        "uq_contribution_legacy_member_competence",
        "contributions",
        ["member_id", "competence"],
        unique=True,
        postgresql_where=sa.text("cycle_id IS NULL"),
        sqlite_where=sa.text("cycle_id IS NULL"),
    )
    op.create_index(
        "uq_contribution_member_cycle_competence",
        "contributions",
        ["member_id", "cycle_id", "competence"],
        unique=True,
        postgresql_where=sa.text("cycle_id IS NOT NULL"),
        sqlite_where=sa.text("cycle_id IS NOT NULL"),
    )
    bind.execute(sa.text(
        "INSERT INTO cycles (start_date, entry_deadline, closing_reference_date, "
        "monthly_amount, months, max_quotas, status) "
        "SELECT :start_date, :entry_deadline, :closing_reference, 150.00, 12, 50, 'OPEN' "
        "WHERE NOT EXISTS (SELECT 1 FROM cycles WHERE start_date = :start_date)"
    ), {"start_date": CYCLE_START, "entry_deadline": ENTRY_DEADLINE, "closing_reference": CLOSING_REFERENCE})


def _sqlite_remove_column(table, column, restore_unique):
    bind = op.get_bind()
    row = bind.execute(
        sa.text(
            "SELECT sql FROM sqlite_master "
            "WHERE type='table' AND name=:name"
        ),
        {"name": table},
    ).scalar_one()

    _, _, definitions = _sqlite_split_definitions(row)

    kept_definitions = []
    removed_columns = 0
    for definition in definitions:
        if (
            not _sqlite_is_table_constraint(definition)
            and _sqlite_column_name(definition) == column.lower()
        ):
            removed_columns += 1
            continue
        kept_definitions.append(definition)

    if removed_columns != 1:
        raise RuntimeError(
            f"Expected exactly one column {column} on {table}; "
            f"found {removed_columns}"
        )

    if restore_unique:
        target_unique = _sqlite_normalize_columns(restore_unique)
        if any(
            _sqlite_unique_columns(definition) == target_unique
            for definition in kept_definitions
            if _sqlite_is_table_constraint(definition)
        ):
            raise RuntimeError(
                f"UNIQUE({restore_unique}) already exists on {table}"
            )

        insert_at = next(
            (
                index
                for index, definition in enumerate(kept_definitions)
                if _sqlite_is_table_constraint(definition)
            ),
            len(kept_definitions),
        )
        kept_definitions.insert(
            insert_at,
            "UNIQUE (" + restore_unique + ")",
        )

    replacement = table + "__a377a_down"
    schema_objects = _sqlite_schema_objects(bind, table)

    sql = _sqlite_build_table_sql(
        row,
        table,
        replacement,
        kept_definitions,
    )
    op.execute(sa.text(sql))

    copy_columns = [
        item[1]
        for item in bind.execute(
            sa.text("PRAGMA table_info(" + table + ")")
        ).fetchall()
        if item[1].lower() != column.lower()
    ]
    op.execute(
        sa.text(
            "INSERT INTO " + replacement
            + " (" + ", ".join(copy_columns) + ") "
            + "SELECT " + ", ".join(copy_columns)
            + " FROM " + table
        )
    )
    op.execute(sa.text("DROP TABLE " + table))
    op.rename_table(replacement, table)

    for schema_sql in schema_objects:
        op.execute(sa.text(schema_sql))


def downgrade():
    bind = op.get_bind()
    count = bind.execute(sa.text(
        "SELECT COUNT(*) FROM quotas WHERE cycle_id IS NOT NULL"
    )).scalar_one()
    count += bind.execute(sa.text(
        "SELECT COUNT(*) FROM contributions WHERE cycle_id IS NOT NULL"
    )).scalar_one()
    if int(count):
        raise RuntimeError("Cannot downgrade 0096: cycle-scoped rows exist")
    op.drop_index("uq_contribution_member_cycle_competence", table_name="contributions")
    op.drop_index("uq_contribution_legacy_member_competence", table_name="contributions")
    op.drop_index("ix_quotas_member_cycle", table_name="quotas")
    op.drop_index("ix_contributions_cycle_id", table_name="contributions")
    op.drop_index("ix_quotas_cycle_id", table_name="quotas")
    if bind.dialect.name == "sqlite":
        _sqlite_remove_column("quotas", "cycle_id", "member_id")
        _sqlite_remove_column("contributions", "cycle_id", "member_id, competence")
    else:
        op.drop_column("quotas", "cycle_id")
        op.drop_column("contributions", "cycle_id")
        op.create_unique_constraint("quotas_member_id_key", "quotas", ["member_id"])
        op.create_unique_constraint("uq_contribution_member_competence", "contributions", ["member_id", "competence"])
    op.drop_table("cycles")
