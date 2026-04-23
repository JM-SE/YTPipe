from fastapi import APIRouter

from app.core.settings import get_settings

router = APIRouter(tags=["status"])


@router.get("/status")
def status() -> dict[str, object]:
    settings = get_settings()
    return {
        "service": settings.app_name,
        "environment": settings.app_env,
        "database_configured": bool(settings.database_url),
        "phase": "foundations",
        "ready": False,
        "details": "Placeholder status endpoint. Full operational visibility arrives in a later phase.",
    }
