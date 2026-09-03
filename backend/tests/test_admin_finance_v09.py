from datetime import date
from decimal import Decimal
from app.models import Expense, MonthlyClosing

def test_decimal_math():
    assert Decimal("100.00") - Decimal("25.50") == Decimal("74.50")

def test_expense_columns_have_db_defaults():
    assert Expense.__table__.c.status.default.arg == "POSTED"
    assert Expense.__table__.c.category.default.arg == "GENERAL"

def test_closing_column_has_db_default():
    assert MonthlyClosing.__table__.c.status.default.arg == "OPEN"
