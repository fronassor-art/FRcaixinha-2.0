from decimal import Decimal

from app.services.reconciliation_v032 import money

def test_money_reconciliation_precision():
    assert money(Decimal("10.005")) == "10.01"
    assert money(Decimal("10.004")) == "10.00"

def test_zero_defaults():
    assert money(None) == "0.00"
