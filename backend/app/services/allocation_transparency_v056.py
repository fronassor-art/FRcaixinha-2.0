from __future__ import annotations
import hashlib, json
from decimal import Decimal
from sqlalchemy.orm import Session
from app.models import AllocationTransparencySnapshot, ResourceAllocationSnapshot


def canonical_json(data) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def explanation_hash(data) -> str:
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


def build_explanation(allocation: dict) -> dict:
    policy = dict(allocation.get("policy") or {})
    items = []
    for item in allocation.get("items", []):
        items.append({
            "member_id": item.get("member_id"),
            "quota_units": str(item.get("quota_units", "0")),
            "payment_score": str(item.get("payment_score", "0")),
            "tenure_score": str(item.get("tenure_score", "0")),
            "risk_decision": item.get("risk_decision"),
            "priority_score": str(item.get("priority_score", "0")),
            "governed_amount": str(item.get("governed_amount", "0")),
            "exposure": str(item.get("exposure", "0")) if item.get("exposure") is not None else None,
            "member_room": str(item.get("member_room", "0")) if item.get("member_room") is not None else None,
        })
    return {
        "schema": "v0.56",
        "group_id": allocation.get("group_id"),
        "capacity": str(allocation.get("capacity", "0")),
        "allocated_total": str(allocation.get("allocated_total", "0")),
        "unallocated": str(allocation.get("unallocated", "0")),
        "decision": allocation.get("decision"),
        "method": "GOVERNED_TRANSPARENT",
        "policy_snapshot": policy,
        "items": items,
        "tie_breaker": policy.get("tie_breaker"),
        "note": "Explicação histórica imutável da recomendação; não cria, aprova ou libera empréstimos.",
    }


def persist_transparency(db: Session, *, resource_snapshot: ResourceAllocationSnapshot, allocation: dict, actor_id: int | None):
    explanation = build_explanation(allocation)
    h = explanation_hash(explanation)
    row = AllocationTransparencySnapshot(
        resource_allocation_snapshot_id=resource_snapshot.id,
        group_id=resource_snapshot.group_id,
        policy_version=int(explanation["policy_snapshot"].get("version", 1)),
        policy_snapshot_json=canonical_json(explanation["policy_snapshot"]),
        input_snapshot_json=canonical_json({
            "capacity": explanation["capacity"],
            "requested_amount": allocation.get("requested_amount"),
            "items": explanation["items"],
        }),
        explanation_json=canonical_json(explanation),
        explanation_hash=h,
        generated_by=actor_id,
    )
    db.add(row)
    return row, explanation, h
