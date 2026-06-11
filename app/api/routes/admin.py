"""
GET /admin/keys — Sarvam key-pool health for the dashboard admin panel.

Keys are always masked; the raw secrets never leave the backend.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/admin", tags=["admin"])


class KeyStatusRead(BaseModel):
    masked: str
    state: str  # healthy | cooldown | disabled
    available_at: float | None = None
    ok_count: int = 0
    err_count: int = 0


@router.get("/keys", response_model=list[KeyStatusRead])
async def list_keys() -> list[KeyStatusRead]:
    from app.core.config import get_settings
    from app.services.stt.key_pool import SarvamKeyPool

    if not get_settings().sarvam_key_list():
        return []
    pool = SarvamKeyPool()
    statuses = await pool.statuses()
    return [KeyStatusRead(**s.__dict__) for s in statuses]
