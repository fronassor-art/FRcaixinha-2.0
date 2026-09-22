from decimal import Decimal
import pytest
from app.services.mercado_pago import serialize_provider_money
from app.services.loan_installment_pix_attempts import (PENDING, APPROVED, EXPIRED, SUPERSEDED, CANCELLED, FAILED, transition)

class Attempt:
    def __init__(self, status=PENDING): self.attempt_status=status

def test_provider_money_is_decimal_only_and_exact_cent():
    assert serialize_provider_money(Decimal("12.30")) == "12.30"
    with pytest.raises(TypeError): serialize_provider_money(12.3)
    with pytest.raises(ValueError): serialize_provider_money(Decimal("12.301"))
    with pytest.raises(ValueError): serialize_provider_money(Decimal("NaN"))

def test_terminal_transition_is_one_way():
    row=Attempt(); transition(row, APPROVED); assert row.attempt_status==APPROVED
    with pytest.raises(ValueError): transition(row, EXPIRED)


def test_transition_rejects_terminal_to_pending():
    row=Attempt(APPROVED)
    with pytest.raises(ValueError): transition(row, PENDING)

@pytest.mark.parametrize("value,expected", [(Decimal("1"),"1.00"),(Decimal("1.20"),"1.20"),(Decimal("0.01"),"0.01"),(Decimal("150.00"),"150.00")])
def test_provider_money_examples(value, expected):
    assert serialize_provider_money(value)==expected

@pytest.mark.parametrize("value", [Decimal("1.001"),Decimal("NaN"),Decimal("Infinity"),Decimal("-0.01"),1.2,"1.20"])
def test_provider_money_rejects_unsafe_values(value):
    with pytest.raises((TypeError,ValueError)):
        serialize_provider_money(value)

def test_provider_client_source_has_no_float_money_path():
    from pathlib import Path
    source=Path(__file__).parents[1].joinpath("app/services/mercado_pago.py").read_text()
    assert "float(" not in source
    assert "round(float" not in source


@pytest.mark.parametrize("target", [APPROVED, EXPIRED, SUPERSEDED, CANCELLED, FAILED])
def test_pending_may_transition_to_each_terminal(target):
    row=Attempt(PENDING)
    transition(row, target)
    assert row.attempt_status == target

@pytest.mark.parametrize("terminal", [APPROVED, EXPIRED, SUPERSEDED, CANCELLED, FAILED])
@pytest.mark.parametrize("target", [PENDING, APPROVED, EXPIRED, SUPERSEDED, CANCELLED, FAILED])
def test_terminal_attempt_never_resurrects_or_changes_terminal(terminal, target):
    row=Attempt(terminal)
    if target == terminal:
        transition(row, target)
        assert row.attempt_status == terminal
    else:
        with pytest.raises(ValueError):
            transition(row, target)
