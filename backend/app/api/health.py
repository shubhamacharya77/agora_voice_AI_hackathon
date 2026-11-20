from typing import Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel

from app.db import Database, get_database

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    database: Literal["ok", "unavailable"]


@router.get("/health", response_model=HealthResponse)
async def health(response: Response, db: Database = Depends(get_database)) -> HealthResponse:
    if await db.ping():
        return HealthResponse(status="ok", database="ok")
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(status="degraded", database="unavailable")
