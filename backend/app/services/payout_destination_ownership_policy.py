"""Provider-neutral, read-only ownership eligibility for PIX destinations."""

from __future__ import annotations

from dataclasses import dataclass
import hmac

from sqlalchemy.exc import MultipleResultsFound
from sqlalchemy.orm import Session

from app.models import Member, User
from app.services.member_payout_destination import get_active_destination
from app.services.payout_destination_crypto import (
    PayoutDestinationCryptoError,
    decrypt_payout_destination,
)
from app.services.payout_destination_validation import (
    PayoutDestinationValidationError,
    normalize_payout_destination,
)


@dataclass(frozen=True)
class PayoutDestinationOwnershipEligibility:
    eligible: bool
    member_id: int
    destination_id: int | None
    key_type: str | None
    reason_code: str | None


def _result(
    *,
    eligible: bool,
    member_id: int,
    destination_id: int | None = None,
    key_type: str | None = None,
    reason_code: str | None = None,
) -> PayoutDestinationOwnershipEligibility:
    return PayoutDestinationOwnershipEligibility(
        eligible=eligible,
        member_id=member_id,
        destination_id=destination_id,
        key_type=key_type,
        reason_code=reason_code,
    )


def evaluate_payout_destination_ownership_eligibility(
    db: Session,
    *,
    member_id: int,
) -> PayoutDestinationOwnershipEligibility:
    """Check local CPF equality without writing or promoting destination state.

    Eligibility is only a precondition for a future external ownership check;
    it is not evidence of ownership and never marks a destination VERIFIED.
    """
    with db.no_autoflush:
        member = db.get(Member, member_id)
        if member is None:
            return _result(
                eligible=False,
                member_id=member_id,
                reason_code="MEMBER_NOT_FOUND",
            )

        user = db.get(User, member.user_id)
        if user is None:
            return _result(
                eligible=False,
                member_id=member.id,
                reason_code="MEMBER_USER_NOT_FOUND",
            )

        try:
            destination = get_active_destination(db, member_id=member.id)
        except MultipleResultsFound:
            return _result(
                eligible=False,
                member_id=member.id,
                reason_code="DESTINATION_STATE_INVALID",
            )
        if destination is None:
            return _result(
                eligible=False,
                member_id=member.id,
                reason_code="DESTINATION_MISSING",
            )

        destination_id = destination.id
        key_type = destination.key_type
        if destination.verification_status != "UNVERIFIED":
            return _result(
                eligible=False,
                member_id=member.id,
                destination_id=destination_id,
                key_type=key_type,
                reason_code="DESTINATION_NOT_UNVERIFIED",
            )
        if key_type != "CPF":
            return _result(
                eligible=False,
                member_id=member.id,
                destination_id=destination_id,
                key_type=key_type,
                reason_code="UNSUPPORTED_KEY_TYPE",
            )

        try:
            member_cpf = normalize_payout_destination("CPF", user.cpf)
        except (PayoutDestinationValidationError, TypeError, ValueError):
            return _result(
                eligible=False,
                member_id=member.id,
                destination_id=destination_id,
                key_type=key_type,
                reason_code="MEMBER_CPF_INVALID",
            )

        try:
            plaintext = decrypt_payout_destination(destination.encrypted_value)
        except (PayoutDestinationCryptoError, TypeError, ValueError):
            return _result(
                eligible=False,
                member_id=member.id,
                destination_id=destination_id,
                key_type=key_type,
                reason_code="DESTINATION_DECRYPTION_FAILED",
            )

        try:
            destination_cpf = normalize_payout_destination("CPF", plaintext)
        except (PayoutDestinationValidationError, TypeError, ValueError):
            return _result(
                eligible=False,
                member_id=member.id,
                destination_id=destination_id,
                key_type=key_type,
                reason_code="DESTINATION_CPF_INVALID",
            )

        if not hmac.compare_digest(destination_cpf, member_cpf):
            return _result(
                eligible=False,
                member_id=member.id,
                destination_id=destination_id,
                key_type=key_type,
                reason_code="DESTINATION_CPF_MISMATCH",
            )

        return _result(
            eligible=True,
            member_id=member.id,
            destination_id=destination_id,
            key_type=key_type,
        )
