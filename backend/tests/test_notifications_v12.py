from app.services.notifications_v12 import send_email


def test_email_requires_smtp(monkeypatch):
    class U: email = "teste@example.com"
    import app.services.notifications_v12 as mod
    monkeypatch.setattr(mod.settings, "smtp_host", None)
    try:
        send_email(U(), "x", "y")
        assert False
    except RuntimeError as exc:
        assert "SMTP" in str(exc)
