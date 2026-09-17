from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import MonthlyClosing


class MonthlyClosingCreationConflict(Exception):
    """A different transaction created the same monthly closing first."""

    MESSAGE = "Fechamento da competência já foi criado por outra transação."

    def __init__(self):
        super().__init__(self.MESSAGE)


def _is_competence_unique_violation(exc: IntegrityError) -> bool:
    original = exc.orig
    constraint_name = getattr(getattr(original, "diag", None), "constraint_name", None)
    if constraint_name is not None:
        return constraint_name == "uq_monthly_closing_competence"

    return str(original) == "UNIQUE constraint failed: monthly_closings.competence"


def create_monthly_closing_or_raise_conflict(
    db: Session, competence
) -> MonthlyClosing:
    try:
        with db.begin_nested():
            closing = MonthlyClosing(competence=competence)
            db.add(closing)
            db.flush()
    except IntegrityError as exc:
        if not _is_competence_unique_violation(exc):
            raise

        winner = (
            db.query(MonthlyClosing)
            .filter(MonthlyClosing.competence == competence)
            .with_for_update()
            .first()
        )
        if winner is None:
            raise RuntimeError(
                "Colisão de competência sem fechamento vencedor persistido."
            ) from exc
        raise MonthlyClosingCreationConflict() from exc

    return closing
