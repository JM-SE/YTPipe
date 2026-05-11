from typing import Any

from fastapi import Depends, FastAPI, Header
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from starlette.responses import HTMLResponse, JSONResponse

from app.api.dependencies import validate_admin_bearer_token
from app.api.routes.activity import router as activity_router
from app.api.routes.auth import router as auth_router
from app.api.routes.channels import router as channels_router
from app.api.routes.health import router as health_router
from app.api.routes.mobile_push import router as mobile_push_router
from app.api.routes.polling import router as polling_router
from app.api.routes.status import router as status_router
from app.api.routes.subscriptions import router as subscriptions_router
from app.core.settings import Settings, get_settings


PROTECTED_OPENAPI_PATH_PREFIXES = ("/internal/",)
PROTECTED_OPENAPI_PATHS = {"/status"}


def create_app(settings_override: Settings | None = None) -> FastAPI:
    settings = settings_override or get_settings()
    settings.validate_runtime_config()

    app = FastAPI(
        title=settings.app_name,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.include_router(auth_router)
    app.include_router(activity_router)
    app.include_router(channels_router)
    app.include_router(health_router)
    app.include_router(mobile_push_router)
    app.include_router(polling_router)
    app.include_router(status_router)
    app.include_router(subscriptions_router)

    def require_docs_access(authorization: str | None = Header(default=None)) -> None:
        if settings.app_env.strip().lower() == "local":
            return
        validate_admin_bearer_token(authorization, settings)

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema

        schema = get_openapi(
            title=settings.app_name,
            version="0.1.0",
            routes=app.routes,
        )
        components = schema.setdefault("components", {})
        security_schemes = components.setdefault("securitySchemes", {})
        security_schemes["bearerAuth"] = {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "opaque",
        }

        for path, path_item in schema.get("paths", {}).items():
            if not _is_protected_openapi_path(path):
                continue
            for operation in path_item.values():
                if isinstance(operation, dict):
                    operation.setdefault("security", [{"bearerAuth": []}])

        app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = custom_openapi  # type: ignore[method-assign]

    @app.get("/openapi.json", include_in_schema=False)
    def openapi_json(_: None = Depends(require_docs_access)) -> JSONResponse:
        return JSONResponse(app.openapi())

    @app.get("/docs", include_in_schema=False)
    def swagger_ui(_: None = Depends(require_docs_access)) -> HTMLResponse:
        return get_swagger_ui_html(
            openapi_url="/openapi.json",
            title=f"{settings.app_name} - Swagger UI",
        )

    @app.get("/redoc", include_in_schema=False)
    def redoc(_: None = Depends(require_docs_access)) -> HTMLResponse:
        return get_redoc_html(
            openapi_url="/openapi.json",
            title=f"{settings.app_name} - ReDoc",
        )

    @app.get("/", tags=["service"])
    def root() -> dict[str, str]:
        return {"service": settings.app_name, "environment": settings.app_env}

    return app


def _is_protected_openapi_path(path: str) -> bool:
    return path in PROTECTED_OPENAPI_PATHS or path.startswith(PROTECTED_OPENAPI_PATH_PREFIXES)


app = create_app()
