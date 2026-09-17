from app.models import MonthlyClosing


def ensure_monthly_closing_open(closing: MonthlyClosing | None) -> None:
    if closing is not None and closing.status == "CLOSED":
        raise ValueError("Competência já encerrada.")
