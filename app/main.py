from fastapi import FastAPI

from app.api.routes.auth import router as auth_router
from app.api.routes.channels import router as channels_router
from app.api.routes.health import router as health_router
from app.api.routes.status import router as status_router
from app.api.routes.subscriptions import router as subscriptions_router
from app.core.settings import get_settings


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(title=settings.app_name)
    app.include_router(auth_router)
    app.include_router(channels_router)
    app.include_router(health_router)
    app.include_router(status_router)
    app.include_router(subscriptions_router)

    @app.get("/", tags=["service"])
    def root() -> dict[str, str]:
        return {"service": settings.app_name, "environment": settings.app_env}

    return app


app = create_app()
