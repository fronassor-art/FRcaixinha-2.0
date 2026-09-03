from app.db.session import SessionLocal
from app.models import User, Group
from app.core.security import hash_password

def seed():
    db = SessionLocal()
    try:
        if not db.query(Group).filter(Group.name == "FRcaixinha 2026").first():
            db.add(Group(name="FRcaixinha 2026"))
        if not db.query(User).filter(User.email == "admin@frcaixinha.local").first():
            db.add(User(
                name="Administrador FRcaixinha",
                email="admin@frcaixinha.local",
                cpf="00000000000",
                password_hash=hash_password("TroqueEstaSenha123!"),
                role="ADMIN"
            ))
        db.commit()
    finally:
        db.close()

if __name__ == "__main__":
    seed()
