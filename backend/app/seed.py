from app.db.session import SessionLocal
from app.models import User, Group
from app.core.seed_safety import read_required_seed_secret, require_seed_execution


_ALLOWED_SEED_ENVS = {"development", "test", "homologation", "staging"}

def seed():
    require_seed_execution(
        seed_name="standard",
        confirmation_env="FRCAIXINHA_ENABLE_STANDARD_SEED",
        allowed_non_production_envs=_ALLOWED_SEED_ENVS,
    )
    password = read_required_seed_secret("FRCAIXINHA_SEED_ADMIN_PASSWORD")
    from app.core.security import hash_password

    db = SessionLocal()
    try:
        if not db.query(Group).filter(Group.name == "FRcaixinha 2026").first():
            db.add(Group(name="FRcaixinha 2026"))
        if not db.query(User).filter(User.email == "admin@frcaixinha.com").first():
            db.add(User(
                name="Administrador FRcaixinha",
                email="admin@frcaixinha.com",
                cpf="00000000000",
                password_hash=hash_password(password),
                role="ADMIN"
            ))
        db.commit()
    finally:
        db.close()

if __name__ == "__main__":
    seed()
