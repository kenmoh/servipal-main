from supabase import AsyncClient
from typing import Optional
from decimal import Decimal

async def log_audit_event(
    supabase: AsyncClient,
    entity_type: str,
    entity_id: str,
    action: str,
    old_value: Optional[dict] = None,
    new_value: Optional[dict] = None,
    change_amount: Optional[Decimal] = None,
    actor_id: Optional[str] = None,
    actor_type: str = "SYSTEM",
    notes: Optional[str] = None,
    request = None,
    
):
    await supabase.table("audit_logs").insert({
        "entity_type": entity_type,
        "entity_id": entity_id,
        "action": action,
        "old_value": old_value,
        "new_value": new_value,
        "change_amount": float(change_amount) if change_amount else None,
        "actor_id": actor_id,
        "actor_type": actor_type,
        "notes": notes,
        "ip_address": request.client.host if request else None,
        "user_agent": request.headers.get("user-agent") if request else None
    }).execute()