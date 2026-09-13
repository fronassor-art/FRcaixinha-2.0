"""enforce the six-installment cap for new loans"""

from alembic import op

revision = "0057_loan_installment_cap_v101"
down_revision = "005668b9e159"
branch_labels = None
depends_on = None


def upgrade():
    # This is configuration data, not historical loan data.
    op.execute(
        "UPDATE groups "
        "SET max_installments = CASE "
        "WHEN max_installments > 6 THEN 6 "
        "WHEN max_installments < 1 THEN 1 "
        "ELSE max_installments END "
        "WHERE max_installments > 6 OR max_installments < 1"
    )
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.create_check_constraint(
            "ck_groups_max_installments_1_6",
            "groups",
            "max_installments >= 1 AND max_installments <= 6",
        )
        op.execute("""
            CREATE FUNCTION enforce_loan_installment_cap()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                IF TG_OP = 'INSERT' THEN
                    IF NEW.installments IS NULL
                       OR NEW.installments < 1
                       OR NEW.installments > 6 THEN
                        RAISE EXCEPTION 'loan installments must be between 1 and 6';
                    END IF;
                    RETURN NEW;
                END IF;

                IF NEW.installments IS NOT DISTINCT FROM OLD.installments THEN
                    RETURN NEW;
                END IF;

                IF OLD.installments > 6 THEN
                    RAISE EXCEPTION 'historical loan installments cannot be changed';
                END IF;

                IF NEW.installments IS NULL
                   OR NEW.installments < 1
                   OR NEW.installments > 6 THEN
                    RAISE EXCEPTION 'loan installments must be between 1 and 6';
                END IF;

                RETURN NEW;
            END;
            $$;
        """)
        op.execute("""
            CREATE TRIGGER trg_enforce_loan_installment_cap
            BEFORE INSERT OR UPDATE OF installments ON loans
            FOR EACH ROW
            EXECUTE FUNCTION enforce_loan_installment_cap();
        """)
    else:
        with op.batch_alter_table("groups") as batch_op:
            batch_op.create_check_constraint(
                "ck_groups_max_installments_1_6",
                "max_installments >= 1 AND max_installments <= 6",
            )


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS trg_enforce_loan_installment_cap ON loans")
        op.execute("DROP FUNCTION IF EXISTS enforce_loan_installment_cap()")
        op.drop_constraint("ck_groups_max_installments_1_6", "groups", type_="check")
    else:
        with op.batch_alter_table("groups") as batch_op:
            batch_op.drop_constraint("ck_groups_max_installments_1_6", type_="check")
