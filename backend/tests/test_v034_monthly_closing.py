from datetime import date
from decimal import Decimal
from app.services.monthly_closing_v034 import money

def test_v034_money_round_half_up():
    assert money(Decimal('1.005')) == '1.01'
