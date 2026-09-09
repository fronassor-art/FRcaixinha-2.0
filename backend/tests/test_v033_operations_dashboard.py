from app.services.reconciliation_v032 import money

def test_v033_money_round_half_up():
    assert money('1.005') == '1.01'
    assert money('10') == '10.00'
