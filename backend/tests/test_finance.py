from decimal import Decimal
from app.services.finance import simple_interest, service_fee

def test_simple_interest():
    assert simple_interest(Decimal("1000"), Decimal("0.20"), 6) == Decimal("1200.00")

def test_service_fee():
    assert service_fee(Decimal("1200"), Decimal("0.10")) == Decimal("120.00")
