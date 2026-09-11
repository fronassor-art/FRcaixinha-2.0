from decimal import Decimal

from app.models import MemberFinancialAccount, MemberFinancialEntry


def test_member_financial_account_model():
    account = MemberFinancialAccount(member_id=1)

    entry = MemberFinancialEntry(
        entry_type="CONTRIBUTION",
        direction="CREDIT",
        amount=Decimal("150.00"),
        reference_type="CONTRIBUTION",
        reference_id="1",
    )

    account.entries.append(entry)

    assert account.member_id == 1
    assert len(account.entries) == 1
    assert account.entries[0].amount == Decimal("150.00")
    assert account.entries[0].direction == "CREDIT"
    assert account.entries[0].entry_type == "CONTRIBUTION"
