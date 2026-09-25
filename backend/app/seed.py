from app.db.session import SessionLocal
from app.models import User, Group
from app.core.cpf import CPFValidationError, normalize_cpf
from app.core.seed_safety import (
    read_required_seed_cpf,
    read_required_seed_secret,
    require_seed_execution,
)


_ALLOWED_SEED_ENVS = {"development", "test", "homologation", "staging"}


def get_or_create_admin(db, cpf, password):
    try:
        cpf = normalize_cpf(cpf)
    except CPFValidationError:
        raise RuntimeError("Configured seed administrator identity is invalid.") from None

    admin = db.query(User).filter(User.email == "admin@frcaixinha.com").first()
    if admin is not None:
        try:
            existing_cpf = normalize_cpf(admin.cpf)
        except CPFValidationError:
            raise RuntimeError("Existing seed administrator identity is invalid.") from None
        if existing_cpf != cpf:
            raise RuntimeError("Existing seed administrator identity conflicts with configuration.")
        return admin

    if db.query(User).filter(User.cpf == cpf).first() is not None:
        raise RuntimeError("Configured seed administrator CPF belongs to another user.")

    from app.core.security import hash_password

    admin = User(
        name="Administrador FRcaixinha",
        email="admin@frcaixinha.com",
        cpf=cpf,
        password_hash=hash_password(password),
        role="ADMIN",
    )
    db.add(admin)
    return admin


def seed():
    require_seed_execution(
        seed_name="standard",
        confirmation_env="FRCAIXINHA_ENABLE_STANDARD_SEED",
        allowed_non_production_envs=_ALLOWED_SEED_ENVS,
    )
    password = read_required_seed_secret("FRCAIXINHA_SEED_ADMIN_PASSWORD")
    admin_cpf = read_required_seed_cpf("FRCAIXINHA_SEED_ADMIN_CPF")

    db = SessionLocal()
    try:
        admin = get_or_create_admin(db, admin_cpf, password)

        if not db.query(Group).filter(Group.name == "FRcaixinha 2026").first():
            db.add(Group(name="FRcaixinha 2026"))
        db.commit()
    finally:
        db.close()

if __name__ == "__main__":
    seed()
