from fastapi.testclient import TestClient

from app.core.settings import Settings
from app.main import create_app


def _staging_settings() -> Settings:
    return Settings(
        APP_ENV="staging",
        APP_SECRET_KEY="staging-secret",
        INTERNAL_API_BEARER_TOKEN="staging-internal-token",
        DATABASE_URL="postgresql+psycopg://user:password@example.neon.tech/ytpipe?sslmode=require",
        GOOGLE_CLIENT_ID="google-client-id",
        GOOGLE_CLIENT_SECRET="google-client-secret",
        GOOGLE_REDIRECT_URI="https://ytpipe-staging.onrender.com/auth/callback",
        EMAIL_DELIVERY_MODE="fake",
    )


def test_local_developer_docs_are_public() -> None:
    client = TestClient(create_app(Settings(APP_ENV="local")))

    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/redoc").status_code == 200


def test_staging_developer_docs_reject_missing_or_wrong_bearer() -> None:
    client = TestClient(create_app(_staging_settings()))

    for path in ("/docs", "/openapi.json", "/redoc"):
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"Authorization": "Bearer wrong-token"}).status_code == 401


def test_staging_developer_docs_accept_correct_bearer() -> None:
    client = TestClient(create_app(_staging_settings()))
    headers = {"Authorization": "Bearer staging-internal-token"}

    assert client.get("/docs", headers=headers).status_code == 200
    assert client.get("/openapi.json", headers=headers).status_code == 200
    assert client.get("/redoc", headers=headers).status_code == 200


def test_openapi_declares_bearer_auth_for_protected_endpoints() -> None:
    client = TestClient(create_app(_staging_settings()))
    response = client.get(
        "/openapi.json",
        headers={"Authorization": "Bearer staging-internal-token"},
    )

    assert response.status_code == 200
    schema = response.json()
    assert schema["components"]["securitySchemes"]["bearerAuth"] == {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "opaque",
    }
    assert schema["paths"]["/status"]["get"]["security"] == [{"bearerAuth": []}]
    assert schema["paths"]["/internal/run-poll"]["post"]["security"] == [{"bearerAuth": []}]
    assert "security" not in schema["paths"]["/health"]["get"]


def test_health_remains_public_and_internal_endpoints_still_require_bearer() -> None:
    client = TestClient(create_app(_staging_settings()))

    assert client.get("/health").status_code == 200
    assert client.get("/status").status_code == 401
    assert client.post("/internal/run-poll").status_code == 401
