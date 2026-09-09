from datetime import date
from decimal import Decimal
from app.services.reports_v10 import money, month_bounds

def test_money():
    assert money(Decimal("12.345")) == "12.35"

def test_month_bounds():
    assert month_bounds(date(2026, 2, 20)) == (date(2026,2,1), date(2026,2,28))
