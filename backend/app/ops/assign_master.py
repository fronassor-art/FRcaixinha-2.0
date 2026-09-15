import argparse
import json

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.session import SessionLocal
from app.models import AuditLog, User


class MasterAssignmentError(ValueError):
    pass


def assign_initial_master(db, target_user_id, operator_user_id, reason, confirm):
    if not confirm:
        raise MasterAssignmentError("confirmação explícita --confirm é obrigatória")
    if not isinstance(reason, str) or not reason.strip():
        raise MasterAssignmentError("motivo não pode ser vazio")
    reason = reason.strip()

    try:
        target = db.execute(
            select(User).where(User.id == target_user_id).with_for_update()
        ).scalar_one_or_none()
        if target is None:
            raise MasterAssignmentError("usuário alvo não encontrado")
        if target.role != "ADMIN":
            raise MasterAssignmentError("usuário alvo deve ser ADMIN")
        if not target.is_active:
            raise MasterAssignmentError("usuário alvo deve estar ativo")

        operator = db.execute(
            select(User).where(User.id == operator_user_id).with_for_update()
        ).scalar_one_or_none()
        if operator is None:
            raise MasterAssignmentError("operador não encontrado")
        if operator.role != "ADMIN":
            raise MasterAssignmentError("operador deve ser ADMIN")
        if not operator.is_active:
            raise MasterAssignmentError("operador deve estar ativo")

        masters = db.execute(
            select(User)
            .where(User.is_master.is_(True), User.is_active.is_(True), User.role == "ADMIN")
            .with_for_update()
        ).scalars().all()
        if masters:
            if len(masters) == 1 and masters[0].id == target.id:
                db.rollback()
                return target
            raise MasterAssignmentError("já existe outro Administrador Master ativo")

        target.is_master = True
        db.add(AuditLog(
            actor_user_id=operator.id,
            action="MASTER_ASSIGNED_INITIAL",
            entity_type="USER",
            entity_id=str(target.id),
            details=json.dumps(
                {
                    "operator_user_id": operator.id,
                    "reason": reason,
                    "target_user_id": target.id,
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ),
        ))
        db.commit()
        return target
    except MasterAssignmentError:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        raise MasterAssignmentError("conflito de unicidade ou transação; nenhuma alteração foi aplicada") from exc
    except Exception:
        db.rollback()
        raise


def _reason(value):
    if not value or not value.strip():
        raise argparse.ArgumentTypeError("motivo não pode ser vazio")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description="Atribui o primeiro Administrador Master")
    parser.add_argument("--target-user-id", type=int, required=True)
    parser.add_argument("--operator-user-id", type=int, required=True)
    parser.add_argument("--reason", type=_reason, required=True)
    parser.add_argument("--confirm", action="store_true", required=True)
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        target = assign_initial_master(
            db, args.target_user_id, args.operator_user_id, args.reason, args.confirm
        )
        print(f"Administrador Master atribuído ao usuário {target.id}.")
        return 0
    except MasterAssignmentError as exc:
        parser.error(str(exc))
    finally:
        db.close()


if __name__ == "__main__":
    main()
